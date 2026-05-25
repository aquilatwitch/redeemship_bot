import asyncio
import json
import logging

import aiohttp
import websockets

import config
from kick_oauth import KickOAuth

logger = logging.getLogger(__name__)

PUSHER_KEY    = "32cbd69e4b950bf97679"
PUSHER_WS_URL = (
    f"wss://ws-us2.pusher.com/app/{PUSHER_KEY}"
    "?protocol=7&client=js&version=8.5.0&flash=false"
)
KICK_API_BASE    = "https://kick.com/api/v2"
KICK_API_V1_BASE = "https://api.kick.com/public/v1"


class KickBot:
    def __init__(self, bot_manager, oauth: KickOAuth):
        self.bot_manager = bot_manager
        self.oauth       = oauth
        self._running    = False
        self._ws         = None

        # slug -> {"chatroom_id": str, "channel_id": str}
        self._channels: dict[str, dict] = {}
        # reverse lookups
        self._chatroom_to_slug: dict[str, str] = {}
        self._channel_to_slug:  dict[str, str] = {}
        # slug -> Kick user_id (fuer v1 API Fallback)
        self._slug_to_user_id:  dict[str, str] = {}

    async def _get_channel_info(self, slug: str) -> dict | None:
        token   = await self.oauth.get_access_token()
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            url = f"{KICK_API_BASE}/channels/{slug}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        return {
                            "channel_id":  str(data.get("id", "")),
                            "chatroom_id": str(data.get("chatroom", {}).get("id", "")),
                        }
                    logger.warning(f"Kick v2 channel lookup {r.status} for {slug} – trying v1")
        except Exception as e:
            logger.warning(f"Kick v2 channel lookup error for {slug}: {e} – trying v1")

        try:
            url = f"https://kick.com/api/v1/channels/{slug}"
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url,
                    headers={"Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        cid = str(data.get("id", ""))
                        rid = str(data.get("chatroom", {}).get("id", "") or cid)
                        if cid:
                            return {"channel_id": cid, "chatroom_id": rid}
                    logger.warning(f"Kick v1-old lookup {r.status} for {slug} – trying new v1")
        except Exception as e:
            logger.warning(f"Kick v1-old lookup error for {slug}: {e} – trying new v1")

        if token and slug in self._slug_to_user_id:
            user_id = self._slug_to_user_id[slug]
            try:
                url = f"{KICK_API_V1_BASE}/channels"
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        url,
                        headers=headers,
                        params={"broadcaster_user_id": user_id},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        if r.status == 200:
                            data = await r.json(content_type=None)
                            ch   = (data.get("data") or [{}])[0]
                            cid  = str(ch.get("id") or ch.get("channel_id") or "")
                            # chatroom_id oft == channel_id bei Kick v1
                            rid  = str(ch.get("chatroom_id") or cid)
                            if cid:
                                return {"channel_id": cid, "chatroom_id": rid}
                        logger.error(f"Kick v1 channel lookup {r.status} for {slug}")
            except Exception as e:
                logger.error(f"Kick v1 channel lookup error for {slug}: {e}")

        logger.error(f"Cannot subscribe to Kick channel {slug}: not found")
        return None

    async def subscribe_to_channel(self, slug: str, giveaway_id: int, user_id: str = "",
                                   channel_id: str = "", chatroom_id: str = ""):
        if slug in self._channels:
            return
        # PHP liefert channel_id/chatroom_id direkt mit (Streamer-Token) – kein eigener API-Call noetig
        if channel_id and chatroom_id:
            info = {"channel_id": channel_id, "chatroom_id": chatroom_id}
        else:
            if user_id:
                self._slug_to_user_id[slug] = user_id
            info = await self._get_channel_info(slug)
        if not info:
            logger.error(f"Cannot subscribe to Kick channel {slug}: not found")
            return

        self._channels[slug]                       = info
        self._chatroom_to_slug[info["chatroom_id"]] = slug
        self._channel_to_slug[info["channel_id"]]   = slug

        if self._ws:
            await self._pusher_subscribe(info)

        logger.info(f"Subscribed to Kick channel {slug} (chatroom {info['chatroom_id']})")

    async def unsubscribe_from_channel(self, slug: str):
        info = self._channels.pop(slug, None)
        if not info:
            return
        self._chatroom_to_slug.pop(info["chatroom_id"], None)
        self._channel_to_slug.pop(info["channel_id"], None)

        if self._ws:
            await self._pusher_unsubscribe(info)

        logger.info(f"Unsubscribed from Kick channel {slug}")

    async def send_message(self, slug: str, message: str):
        token = await self.oauth.get_access_token()
        if not token:
            logger.warning(f"No Kick token – cannot send message to {slug}")
            return
        info = self._channels.get(slug)
        if not info:
            logger.warning(f"No channel info for {slug} – cannot send message")
            return

        url     = f"{KICK_API_BASE}/messages/send/{info['chatroom_id']}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }
        body = {"content": message, "type": "message"}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status not in (200, 201):
                        text = await r.text()
                        logger.error(f"Kick send_message failed {r.status}: {text}")
        except Exception as e:
            logger.error(f"Kick send_message error to {slug}: {e}")

    async def run(self):
        self._running = True
        while self._running:
            try:
                async with websockets.connect(PUSHER_WS_URL) as ws:
                    self._ws = ws
                    logger.info("Kick: connected to Pusher")

                    for info in self._channels.values():
                        await self._pusher_subscribe(info)

                    async for raw in ws:
                        await self._handle_raw(raw)

            except Exception as e:
                logger.error(f"Kick Pusher error: {e} – reconnecting in 5 s")
                self._ws = None
                await asyncio.sleep(5)

    async def _pusher_subscribe(self, info: dict):
        try:
            await self._ws.send(json.dumps({
                "event": "pusher:subscribe",
                "data":  {"auth": "", "channel": f"chatrooms.{info['chatroom_id']}.v2"},
            }))
            await self._ws.send(json.dumps({
                "event": "pusher:subscribe",
                "data":  {"auth": "", "channel": f"channel.{info['channel_id']}"},
            }))
        except Exception as e:
            logger.warning(f"Pusher subscribe error: {e}")

    async def _pusher_unsubscribe(self, info: dict):
        try:
            await self._ws.send(json.dumps({
                "event": "pusher:unsubscribe",
                "data":  {"channel": f"chatrooms.{info['chatroom_id']}.v2"},
            }))
            await self._ws.send(json.dumps({
                "event": "pusher:unsubscribe",
                "data":  {"channel": f"channel.{info['channel_id']}"},
            }))
        except Exception as e:
            logger.warning(f"Pusher unsubscribe error: {e}")

    async def _handle_raw(self, raw: str):
        try:
            msg     = json.loads(raw)
            event   = msg.get("event", "")
            channel = msg.get("channel", "")

            if event == "App\\Events\\ChatMessageEvent":
                chatroom_id = channel.removeprefix("chatrooms.").removesuffix(".v2")
                slug        = self._chatroom_to_slug.get(chatroom_id)
                if not slug:
                    return
                data       = json.loads(msg.get("data", "{}"))
                sender     = data.get("sender", {})
                user_id    = str(sender.get("id", ""))
                user_login = sender.get("slug", "") or sender.get("username", "")
                text       = data.get("content", "").strip().lower()
                await self.bot_manager.handle_join_kick(slug, user_id, user_login, text)

            elif event == "App\\Events\\StopStreamBroadcast":
                if channel.startswith("channel."):
                    channel_id = channel.removeprefix("channel.")
                    slug       = self._channel_to_slug.get(channel_id)
                    if slug:
                        logger.info(f"Kick stream offline: {slug}")
                        await self.bot_manager.handle_offline_kick(slug)

        except Exception as e:
            logger.error(f"Kick _handle_raw error: {e}")
