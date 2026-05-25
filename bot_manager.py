import asyncio
import logging
from datetime import datetime
from redeemship_client import RedeemShipClient

logger = logging.getLogger(__name__)


class BotManager:
    def __init__(self, twitch_bot, kick_bot, rs_client: RedeemShipClient):
        self.twitch_bot   = twitch_bot
        self.kick_bot     = kick_bot
        self.discord_bot  = None
        self.rs_client    = rs_client
        # Keys: "twitch:{broadcaster_twitch_id}"  or  "kick:{broadcaster_kick_slug}"
        self.active_giveaways: dict = {}
        # Keys: giveaway_id (int) – Discord giveaways tracked by message reaction
        self.active_discord_giveaways: dict = {}

    async def sync_with_api(self):
        while True:
            try:
                remote     = await self.rs_client.get_active_giveaways()
                remote_map = {}

                for g in remote:
                    platform = g.get("platform", "twitch")
                    if platform == "both":
                        twitch_id = g.get("broadcaster_twitch_id", "")
                        kick_slug = g.get("broadcaster_kick_slug", "")
                        if twitch_id:
                            remote_map[f"twitch:{twitch_id}"] = g
                        if kick_slug:
                            remote_map[f"kick:{kick_slug}"] = g
                    elif platform == "kick":
                        remote_map[f"kick:{g['broadcaster_kick_slug']}"] = g
                    else:
                        remote_map[f"twitch:{g['broadcaster_twitch_id']}"] = g

                # Subscribe new channels
                for key, g in remote_map.items():
                    if key not in self.active_giveaways:
                        await self._subscribe(key, g)
                        await self._send(key, f"Giveaway gestartet! Schreib {g['chat_command']} um teilzunehmen!")
                    self.active_giveaways[key] = g

                # Unsubscribe removed channels
                for key in list(self.active_giveaways):
                    if key not in remote_map:
                        await self._unsubscribe(key)
                        del self.active_giveaways[key]

            except Exception:
                logger.exception("sync_with_api error")

            await asyncio.sleep(10)

    async def handle_join(self, broadcaster_twitch_id: str, user_id: str, user_login: str, text: str):
        key     = f"twitch:{broadcaster_twitch_id}"
        giveaway = self.active_giveaways.get(key)
        if not giveaway:
            return
        if text != giveaway["chat_command"].lower():
            return
        await self._record_entry(key, giveaway, user_id, user_login, "twitch")

    async def handle_join_kick(self, slug: str, user_id: str, user_login: str, text: str):
        key      = f"kick:{slug}"
        giveaway = self.active_giveaways.get(key)
        if not giveaway:
            return
        if text != giveaway["chat_command"].lower():
            return
        await self._record_entry(key, giveaway, user_id, user_login, "kick")

    async def handle_offline(self, broadcaster_twitch_id: str):
        try:
            await self.rs_client.report_offline("twitch", broadcaster_twitch_id)
        except Exception as e:
            logger.error(f"handle_offline error: {e}")

    async def handle_offline_kick(self, slug: str):
        try:
            await self.rs_client.report_offline("kick", slug)
        except Exception as e:
            logger.error(f"handle_offline_kick error: {e}")

    async def sync_discord_giveaways(self):
        while True:
            try:
                if self.discord_bot and self.discord_bot.is_ready():
                    giveaways = await self.rs_client.get_active_discord_giveaways()
                    active_ids = set()

                    for g in giveaways:
                        gid = int(g["id"])
                        active_ids.add(gid)
                        msg_id = g.get("message_id")

                        if g["status"] == "scheduled" and not msg_id:
                            scheduled = g.get("scheduled_start")
                            if scheduled:
                                try:
                                    if datetime.now() >= datetime.fromisoformat(scheduled):
                                        await self._start_discord_giveaway(g)
                                except Exception:
                                    pass
                            continue

                        if g["status"] == "active" and msg_id:
                            if msg_id not in self.discord_bot.active_messages:
                                self.discord_bot.register_message(msg_id, g)
                            else:
                                self.discord_bot.active_messages[msg_id] = g

                            if g.get("scheduled_end"):
                                try:
                                    if datetime.now() >= datetime.fromisoformat(g["scheduled_end"]):
                                        result = await self.rs_client.discord_giveaway_auto_end(gid)
                                        if result.get("success"):
                                            ch  = result.get("channel_id") or g.get("channel_id", "")
                                            mid = result.get("message_id") or g.get("message_id", "")
                                            if mid:
                                                await self.discord_bot.end_giveaway_message(
                                                    ch, mid, result.get("winner_login"), gid
                                                )
                                                self.discord_bot.unregister_message(mid)
                                            if result.get("winner_discord_id") and result.get("slug"):
                                                import config as cfg
                                                dm_msg = (
                                                    f"🎉 Glückwunsch! Du hast das Giveaway gewonnen! "
                                                    f"Hol dir deinen Gewinn hier: "
                                                    f"{cfg.REDEEMSHIP_API_URL}/streamer/{result['slug']}/"
                                                )
                                                await self.discord_bot.send_dm(
                                                    result["winner_discord_id"], dm_msg
                                                )
                                except Exception:
                                    logger.exception("discord auto_end error for giveaway %s", gid)

                    for msg_id in list(self.discord_bot.active_messages.keys()):
                        gid = int(self.discord_bot.active_messages[msg_id]["id"])
                        if gid not in active_ids:
                            self.discord_bot.unregister_message(msg_id)

            except Exception:
                logger.exception("sync_discord_giveaways error")

            await asyncio.sleep(10)

    async def _start_discord_giveaway(self, giveaway: dict):
        if not self.discord_bot:
            return
        channel_id = giveaway.get("channel_id", "")
        msg_id = await self.discord_bot.post_giveaway_message(channel_id, giveaway)
        if msg_id:
            await self.rs_client.discord_giveaway_started(int(giveaway["id"]), msg_id)
            self.discord_bot.register_message(msg_id, {**giveaway, "message_id": msg_id})

    async def handle_discord_button_click(self, giveaway_id: int, user_id: str, user_login: str) -> str:
        """Toggle: erster Klick = einschreiben, zweiter Klick = austragen."""
        try:
            result = await self.rs_client.post_discord_entry(giveaway_id, user_id, user_login)
            if result.get("already_entered"):
                await self.rs_client.remove_discord_entry(giveaway_id, user_id)
                return "left"
            if result.get("success"):
                return "joined"
            return "error"
        except Exception:
            logger.exception("handle_discord_button_click error")
            return "error"

    async def announce_winner(self, platform: str, broadcaster_id: str, winner_login: str, slug: str,
                              broadcaster_twitch_id: str = "", broadcaster_kick_slug: str = ""):
        import config as cfg
        msg = f"@{winner_login} Du hast gewonnen! Bestell dein Produkt: {cfg.REDEEMSHIP_API_URL}/streamer/{slug}/"
        if platform == "both":
            if broadcaster_twitch_id:
                await self._send(f"twitch:{broadcaster_twitch_id}", msg)
            if broadcaster_kick_slug:
                await self._send(f"kick:{broadcaster_kick_slug}", msg)
        else:
            await self._send(f"{platform}:{broadcaster_id}", msg)

    async def _record_entry(self, key: str, giveaway: dict, user_id: str, user_login: str, platform: str):
        try:
            result = await self.rs_client.post_entry(giveaway["id"], user_id, user_login, platform)
            if result.get("success") and not result.get("already_entered"):
                await self._send(key, f"@{user_login} Du bist im Lostopf!")
        except Exception as e:
            logger.error(f"_record_entry error: {e}")

    async def _subscribe(self, key: str, giveaway: dict):
        platform, bid = key.split(":", 1)
        try:
            if platform == "kick":
                user_id    = str(giveaway.get("broadcaster_kick_user_id", ""))
                channel_id = str(giveaway.get("kick_channel_id",  ""))
                chatroom_id = str(giveaway.get("kick_chatroom_id", ""))
                await self.kick_bot.subscribe_to_channel(bid, giveaway["id"], user_id, channel_id, chatroom_id)
            else:
                await self.twitch_bot.subscribe_to_channel(bid, giveaway["id"])
        except Exception as e:
            logger.error(f"_subscribe error for {key}: {e}")

    async def _unsubscribe(self, key: str):
        platform, bid = key.split(":", 1)
        try:
            if platform == "kick":
                await self.kick_bot.unsubscribe_from_channel(bid)
            else:
                await self.twitch_bot.unsubscribe_from_channel(bid)
        except Exception as e:
            logger.error(f"_unsubscribe error for {key}: {e}")

    async def _send(self, key: str, message: str):
        platform, bid = key.split(":", 1)
        try:
            if platform == "kick":
                await self.kick_bot.send_message(bid, message)
            else:
                await self.twitch_bot.send_message(bid, message)
        except Exception as e:
            logger.error(f"_send error for {key}: {e}")
