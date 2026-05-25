import os
from dotenv import load_dotenv

load_dotenv()

# Twitch
BOT_TWITCH_CLIENT_ID     = os.getenv("BOT_TWITCH_CLIENT_ID", "")
BOT_TWITCH_CLIENT_SECRET = os.getenv("BOT_TWITCH_CLIENT_SECRET", "")
BOT_USER_ID              = os.getenv("BOT_USER_ID", "")
BOT_USER_TOKEN           = os.getenv("BOT_USER_TOKEN", "")
BOT_USER_REFRESH_TOKEN   = os.getenv("BOT_USER_REFRESH_TOKEN", "")

# Kick OAuth2
KICK_CLIENT_ID     = os.getenv("KICK_CLIENT_ID", "")
KICK_CLIENT_SECRET = os.getenv("KICK_CLIENT_SECRET", "")

# Discord Bot
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

# RedeemShip
REDEEMSHIP_API_URL = os.getenv("REDEEMSHIP_API_URL", "https://")
BOT_API_SECRET     = os.getenv("BOT_API_SECRET", "")

# API server
API_SERVER_HOST = os.getenv("API_SERVER_HOST", "127.0.0.1")
API_SERVER_PORT = int(os.getenv("API_SERVER_PORT", ""))
