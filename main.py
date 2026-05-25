import asyncio
import logging
import uvicorn
from twitch_client import TwitchBot
from kick_client import KickBot
from kick_oauth import KickOAuth
from discord_client import DiscordBot
from bot_manager import BotManager
from redeemship_client import RedeemShipClient
import api_server
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    rs_client   = RedeemShipClient()
    kick_oauth  = KickOAuth()
    bot_manager = BotManager(None, None, rs_client)

    twitch_bot  = TwitchBot(bot_manager)
    kick_bot    = KickBot(bot_manager, kick_oauth)

    bot_manager.twitch_bot = twitch_bot
    bot_manager.kick_bot   = kick_bot

    await twitch_bot.initialize()

    if not kick_oauth.is_authorized():
        logger.warning(
            "Kick not authorized. Visit http://%s:%d/kick/auth?X-Bot-Secret=<secret> "
            "to start the OAuth2 flow.",
            config.API_SERVER_HOST, config.API_SERVER_PORT,
        )

    discord_bot = None
    if config.DISCORD_BOT_TOKEN:
        discord_bot = DiscordBot(bot_manager)
        bot_manager.discord_bot = discord_bot
        logger.info("Discord bot enabled")
    else:
        logger.warning("DISCORD_BOT_TOKEN not set – Discord giveaways disabled")

    api_server.init_app(bot_manager, twitch_bot, kick_bot, kick_oauth, discord_bot)

    uv_config = uvicorn.Config(
        api_server.app,
        host=config.API_SERVER_HOST,
        port=config.API_SERVER_PORT,
        log_level="warning",
    )
    uv_server = uvicorn.Server(uv_config)

    logger.info(f"Starting API server on {config.API_SERVER_HOST}:{config.API_SERVER_PORT}")

    tasks = [
        bot_manager.sync_with_api(),
        bot_manager.sync_discord_giveaways(),
        kick_bot.run(),
        uv_server.serve(),
    ]
    if discord_bot:
        tasks.append(discord_bot.run_bot())

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
