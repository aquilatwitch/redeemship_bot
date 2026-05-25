import asyncio
import logging
from twitchAPI.twitch import Twitch
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.type import AuthScope
from twitchAPI.object.eventsub import ChannelChatMessageEvent, StreamOfflineEvent
import config

logger = logging.getLogger(__name__)


class TwitchBot:
    def __init__(self, bot_manager):
        self.bot_manager       = bot_manager
        self.twitch: Twitch    = None
        self.eventsub          = None
        self._eventsub_started = False
        self._subscriptions: dict[str, list[str]] = {}  # broadcaster_id -> [sub_ids]

    async def initialize(self):
        self.twitch = await Twitch(config.BOT_TWITCH_CLIENT_ID, config.BOT_TWITCH_CLIENT_SECRET)
        await self.twitch.set_user_authentication(
            config.BOT_USER_TOKEN,
            [AuthScope.USER_WRITE_CHAT, AuthScope.USER_BOT, AuthScope.USER_READ_CHAT],
            config.BOT_USER_REFRESH_TOKEN,
        )
        # Alte EventSub-Subscriptions vom vorherigen Run aufraumen
        try:
            result = await self.twitch.get_eventsub_subscriptions()
            for sub in result.data:
                await self.twitch.delete_eventsub_subscription(sub.id)
            logger.info("Alte EventSub-Subscriptions bereinigt")
        except Exception as e:
            logger.warning(f"Konnte Subscriptions nicht bereinigen: {e}")
        logger.info("TwitchBot initialized")

    async def _ensure_eventsub(self):
        if not self._eventsub_started:
            self.eventsub = EventSubWebsocket(self.twitch)
            self.eventsub.start()
            self._eventsub_started = True
            await asyncio.sleep(1)

    async def subscribe_to_channel(self, broadcaster_id: str, giveaway_id: int):
        if broadcaster_id in self._subscriptions:
            return
        await self._ensure_eventsub()
        sub_ids = []
        try:
            sub_chat = await self.eventsub.listen_channel_chat_message(
                broadcaster_id, config.BOT_USER_ID, self.on_chat_message
            )
            sub_ids.append(sub_chat)
            sub_offline = await self.eventsub.listen_stream_offline(
                broadcaster_id, self.on_stream_offline
            )
            sub_ids.append(sub_offline)
            self._subscriptions[broadcaster_id] = sub_ids
            logger.info(f"Subscribed to channel {broadcaster_id}")
        except Exception as e:
            logger.error(f"Failed to subscribe to {broadcaster_id}: {e}")

    async def unsubscribe_from_channel(self, broadcaster_id: str):
        sub_ids = self._subscriptions.pop(broadcaster_id, [])
        for sid in sub_ids:
            try:
                await self.twitch.delete_eventsub_subscription(sid)
            except Exception as e:
                if 'not found' not in str(e).lower():
                    logger.warning(f"Unsubscribe error for {broadcaster_id}: {e}")
        logger.info(f"Unsubscribed from channel {broadcaster_id}")

    async def send_message(self, broadcaster_id: str, message: str):
        try:
            await self.twitch.send_chat_message(broadcaster_id, config.BOT_USER_ID, message)
        except Exception as e:
            logger.error(f"Failed to send message to {broadcaster_id}: {e}")

    async def on_chat_message(self, event: ChannelChatMessageEvent):
        broadcaster_id = event.event.broadcaster_user_id
        user_id        = event.event.chatter_user_id
        user_login     = event.event.chatter_user_login
        text           = (event.event.message.text or "").strip().lower()
        await self.bot_manager.handle_join(broadcaster_id, user_id, user_login, text)

    async def on_stream_offline(self, event: StreamOfflineEvent):
        broadcaster_id = event.event.broadcaster_user_id
        logger.info(f"Stream offline: {broadcaster_id}")
        await self.bot_manager.handle_offline(broadcaster_id)
        await self.unsubscribe_from_channel(broadcaster_id)
