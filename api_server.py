import logging
import secrets
import time

import aiohttp
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
import config

logger = logging.getLogger(__name__)

app = FastAPI()

_bot_manager = None
_twitch_bot  = None
_kick_bot    = None
_kick_oauth  = None

# PKCE state token (single-use, in-memory)
_oauth_state: str | None = None

# Cache for RedeemShip API reachability (TTL: 30s)
_api_reachable:  bool | None = None
_api_last_check: float       = 0


_discord_bot = None


def init_app(bot_manager, twitch_bot, kick_bot, kick_oauth, discord_bot=None):
    global _bot_manager, _twitch_bot, _kick_bot, _kick_oauth, _discord_bot
    _bot_manager = bot_manager
    _twitch_bot  = twitch_bot
    _kick_bot    = kick_bot
    _kick_oauth  = kick_oauth
    _discord_bot = discord_bot


def _platform_keys(platform: str, body: dict) -> list[str]:
    """Gibt die active_giveaways-Keys fuer einen Giveaway-Endpunkt zurueck.
    Bei 'both' werden zwei Keys (twitch + kick) zurueckgegeben."""
    if platform == "both":
        twitch_id = body.get("broadcaster_twitch_id", "")
        kick_slug = body.get("broadcaster_kick_slug", "")
        return [k for k in [
            f"twitch:{twitch_id}" if twitch_id else None,
            f"kick:{kick_slug}"   if kick_slug  else None,
        ] if k]
    bid = body.get("broadcaster_id", "")
    return [f"{platform}:{bid}"]


def _twitch_connected() -> bool:
    return _twitch_bot is not None and _twitch_bot.twitch is not None


def _kick_connected() -> bool:
    return _kick_bot is not None and _kick_bot._ws is not None


async def _check_api_reachable() -> bool:
    global _api_reachable, _api_last_check
    if time.monotonic() - _api_last_check < 30:
        return bool(_api_reachable)
    try:
        url = f"{config.REDEEMSHIP_API_URL}/api/bot/state.php"
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url,
                headers={"X-Bot-Secret": config.BOT_API_SECRET},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                _api_reachable = r.status < 500
    except Exception:
        _api_reachable = False
    _api_last_check = time.monotonic()
    return bool(_api_reachable)


def _check_secret(request: Request):
    if request.headers.get("X-Bot-Secret") != config.BOT_API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/giveaway/start")
async def giveaway_start(request: Request):
    _check_secret(request)
    body     = await request.json()
    platform = body.get("platform", "twitch")

    giveaway = {
        "id":           body.get("giveaway_id"),
        "platform":     platform,
        "slug":         body.get("slug", ""),
        "chat_command": body.get("chat_command", "!join"),
        "status":       "active",
    }

    if platform == "both":
        twitch_id = body.get("broadcaster_twitch_id", "")
        kick_slug = body.get("broadcaster_kick_slug", "")
        giveaway["broadcaster_twitch_id"] = twitch_id
        giveaway["broadcaster_kick_slug"] = kick_slug
        giveaway["kick_channel_id"]       = body.get("kick_channel_id",  "")
        giveaway["kick_chatroom_id"]      = body.get("kick_chatroom_id", "")
        keys = [k for k in [
            f"twitch:{twitch_id}" if twitch_id else None,
            f"kick:{kick_slug}"   if kick_slug  else None,
        ] if k]
    elif platform == "kick":
        bid = body.get("broadcaster_id", "")
        giveaway["broadcaster_kick_slug"] = bid
        giveaway["kick_channel_id"]       = body.get("kick_channel_id",  "")
        giveaway["kick_chatroom_id"]      = body.get("kick_chatroom_id", "")
        keys = [f"kick:{bid}"]
    else:
        bid = body.get("broadcaster_id", "")
        giveaway["broadcaster_twitch_id"] = bid
        keys = [f"twitch:{bid}"]

    chat_msg = f"Giveaway gestartet! Schreib {giveaway['chat_command']} um teilzunehmen!"
    for key in keys:
        if key not in _bot_manager.active_giveaways:
            await _bot_manager._subscribe(key, giveaway)
        _bot_manager.active_giveaways[key] = giveaway
        await _bot_manager._send(key, chat_msg)
    return {"success": True}


@app.post("/giveaway/pause")
async def giveaway_pause(request: Request):
    _check_secret(request)
    body     = await request.json()
    platform = body.get("platform", "twitch")
    keys     = _platform_keys(platform, body)
    for key in keys:
        _bot_manager.active_giveaways.pop(key, None)
        await _bot_manager._unsubscribe(key)
    return {"success": True}


@app.post("/giveaway/stop")
async def giveaway_stop(request: Request):
    _check_secret(request)
    body     = await request.json()
    platform = body.get("platform", "twitch")
    keys     = _platform_keys(platform, body)
    for key in keys:
        _bot_manager.active_giveaways.pop(key, None)
        await _bot_manager._unsubscribe(key)
        await _bot_manager._send(key, "Giveaway beendet!")
    return {"success": True}


@app.post("/giveaway/announce")
async def giveaway_announce(request: Request):
    _check_secret(request)
    body         = await request.json()
    platform     = body.get("platform", "twitch")
    winner_login = body.get("winner_login", "")
    slug         = body.get("slug", "")
    await _bot_manager.announce_winner(
        platform,
        body.get("broadcaster_id", ""),
        winner_login,
        slug,
        broadcaster_twitch_id=body.get("broadcaster_twitch_id", ""),
        broadcaster_kick_slug=body.get("broadcaster_kick_slug", ""),
    )
    return {"success": True}


# ------------------------------------------------------------------ #
#  Kick OAuth2 flow                                                    #
# ------------------------------------------------------------------ #

@app.post("/kick/chat")
async def kick_chat(request: Request):
    """PHP ruft diesen Endpoint auf um Kick-Chat-Nachrichten zu senden.
    Nutzt den Bot-User-Token (kick_tokens.json) statt App Token."""
    _check_secret(request)
    body             = await request.json()
    broadcaster_id   = body.get("broadcaster_user_id", 0)
    message          = body.get("message", "")
    if not broadcaster_id or not message:
        raise HTTPException(status_code=400, detail="Missing broadcaster_user_id or message")

    token = await _kick_oauth.get_access_token() if _kick_oauth else None
    if not token:
        raise HTTPException(status_code=503, detail="No Kick user token available")

    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://api.kick.com/public/v1/chat",
            json={"content": message, "type": "user", "broadcaster_user_id": int(broadcaster_id)},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as r:
            if r.status not in (200, 201):
                text = await r.text()
                logger.error(f"Kick chat send failed {r.status}: {text}")
                raise HTTPException(status_code=r.status, detail=text)
    return {"success": True}


@app.get("/kick/auth")
async def kick_auth(request: Request):
    _check_secret(request)
    global _oauth_state
    _oauth_state = secrets.token_urlsafe(16)
    url = _kick_oauth.get_auth_url(_oauth_state)
    return RedirectResponse(url)


@app.get("/kick/callback")
async def kick_callback(request: Request):
    params = request.query_params
    state  = params.get("state", "")
    code   = params.get("code", "")

    if state != _oauth_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    ok = await _kick_oauth.handle_callback(code)
    if not ok:
        raise HTTPException(status_code=502, detail="Token exchange failed – check logs")
    return {"success": True, "message": "Kick authorized! Tokens saved to kick_tokens.json"}


@app.get("/kick/status")
async def kick_status(request: Request):
    _check_secret(request)
    return {
        "authorized": _kick_oauth.is_authorized(),
        "active_kick_channels": [
            k.removeprefix("kick:")
            for k in _bot_manager.active_giveaways
            if k.startswith("kick:")
        ],
    }

@app.post("/discord/giveaway/start")
async def discord_giveaway_start(request: Request):
    """PHP ruft auf wenn ein Discord-Giveaway sofort gestartet werden soll.
    Bot postet das Embed und gibt die message_id zurueck."""
    _check_secret(request)
    if not _discord_bot or not _discord_bot.is_ready():
        raise HTTPException(status_code=503, detail="Discord bot not connected")
    body     = await request.json()
    msg_id   = await _discord_bot.post_giveaway_message(body.get("channel_id", ""), body)
    if not msg_id:
        raise HTTPException(status_code=500, detail="Failed to post message")
    giveaway = {**body, "message_id": msg_id}
    _discord_bot.register_message(msg_id, giveaway)
    return {"success": True, "message_id": msg_id}


@app.post("/discord/giveaway/end")
async def discord_giveaway_end(request: Request):
    """PHP ruft auf wenn ein Discord-Giveaway beendet wird (Edit des Embeds)."""
    _check_secret(request)
    if not _discord_bot or not _discord_bot.is_ready():
        return {"success": False, "detail": "Discord bot not connected"}
    body         = await request.json()
    message_id   = body.get("message_id", "")
    channel_id   = body.get("channel_id", "")
    winner_login = body.get("winner_login")
    if message_id:
        giveaway    = _discord_bot.active_messages.get(message_id)
        giveaway_id = int(giveaway["id"]) if giveaway else None
        _discord_bot.unregister_message(message_id)
        await _discord_bot.end_giveaway_message(channel_id, message_id, winner_login, giveaway_id)
    return {"success": True}


@app.post("/discord/giveaway/announce")
async def discord_giveaway_announce(request: Request):
    """PHP ruft auf um dem Gewinner eine DM zu senden."""
    _check_secret(request)
    if not _discord_bot or not _discord_bot.is_ready():
        return {"success": False, "dm_sent": False, "detail": "Discord bot not connected"}
    body         = await request.json()
    user_id      = body.get("discord_user_id", "")
    slug         = body.get("slug", "")
    message      = (
        f"Herzlichen Glueckwunsch! Du hast das Giveaway gewonnen! "
        f"Bestell deinen Gewinn hier: {config.REDEEMSHIP_API_URL}/streamer/{slug}/"
    )
    dm_sent = await _discord_bot.send_dm(user_id, message)
    return {"success": True, "dm_sent": dm_sent}


@app.post("/discord/channels")
async def discord_channels(request: Request):
    """Gibt Textkanale fuer eine Guild zurueck (fuer den Kanal-Selektor im Dashboard)."""
    _check_secret(request)
    if not _discord_bot or not _discord_bot.is_ready():
        return {"channels": [], "error": "Discord bot not connected"}
    body     = await request.json()
    guild_id = body.get("guild_id", "")
    if not guild_id:
        raise HTTPException(status_code=400, detail="Missing guild_id")
    channels = await _discord_bot.get_guild_channels(guild_id)
    return {"channels": channels}


@app.post("/discord/guilds")
async def discord_guilds(request: Request):
    """Gibt alle Server zurueck, in denen der Bot Mitglied ist."""
    _check_secret(request)
    if not _discord_bot or not _discord_bot.is_ready():
        return {"guilds": []}
    guilds = await _discord_bot.get_guilds()
    return {"guilds": guilds}

