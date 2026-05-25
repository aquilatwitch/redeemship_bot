import aiohttp
import config


class RedeemShipClient:
    def __init__(self):
        self._headers = {"X-Bot-Secret": config.BOT_API_SECRET}
        self._base    = config.REDEEMSHIP_API_URL

    async def get_active_giveaways(self) -> list:
        url = f"{self._base}/api/bot/state.php"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=self._headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json(content_type=None)
                return data.get("active_giveaways", [])

    async def post_entry(self, giveaway_id: int, user_id: str, user_login: str, platform: str = "twitch") -> dict:
        url = f"{self._base}/api/bot/entry.php"
        if platform == "kick":
            body = {"giveaway_id": giveaway_id, "kick_user_id": user_id, "kick_login": user_login}
        else:
            body = {"giveaway_id": giveaway_id, "twitch_user_id": user_id, "twitch_login": user_login}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=self._headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return await r.json(content_type=None)

    async def report_offline(self, platform: str, broadcaster_id: str) -> bool:
        url = f"{self._base}/api/bot/offline.php"
        if platform == "kick":
            body = {"broadcaster_kick_slug": broadcaster_id}
        else:
            body = {"broadcaster_twitch_id": broadcaster_id}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=self._headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return r.status == 200

    async def get_active_discord_giveaways(self) -> list:
        url = f"{self._base}/api/discord/giveaways.php"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=self._headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json(content_type=None)
                return data.get("giveaways", [])

    async def post_discord_entry(self, giveaway_id: int, user_id: str, user_login: str) -> dict:
        url = f"{self._base}/api/discord/entry.php"
        body = {"giveaway_id": giveaway_id, "discord_user_id": user_id, "discord_login": user_login}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=self._headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return await r.json(content_type=None)

    async def remove_discord_entry(self, giveaway_id: int, user_id: str) -> bool:
        url = f"{self._base}/api/discord/entry-remove.php"
        body = {"giveaway_id": giveaway_id, "discord_user_id": user_id}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=self._headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return r.status == 200

    async def discord_giveaway_started(self, giveaway_id: int, message_id: str) -> bool:
        url = f"{self._base}/api/discord/giveaway-started.php"
        body = {"giveaway_id": giveaway_id, "message_id": message_id}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=self._headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return r.status == 200

    async def discord_giveaway_auto_end(self, giveaway_id: int) -> dict:
        url = f"{self._base}/api/discord/giveaway-auto-end.php"
        body = {"giveaway_id": giveaway_id}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=body, headers=self._headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        return await r.json()
        except Exception:
            pass
        return {"success": False}
