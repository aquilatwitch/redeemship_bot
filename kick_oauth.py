import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

import config

logger = logging.getLogger(__name__)

TOKEN_FILE = Path(__file__).parent / "kick_tokens.json"

# Kick OAuth2 endpoints
AUTH_URL   = "https://id.kick.com/oauth/authorize"
TOKEN_URL  = "https://id.kick.com/oauth/token"
SCOPES     = "chat:write chat:read channel:read"


def _load_tokens() -> dict:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_tokens(tokens: dict):
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    logger.info("Kick tokens saved")


def _make_pkce() -> tuple[str, str]:
    verifier  = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class KickOAuth:
    def __init__(self):
        self._tokens: dict       = _load_tokens()
        self._verifier: str | None = None
        self._refresh_lock       = asyncio.Lock()

    def get_auth_url(self, state: str) -> str:
        self._verifier, challenge = _make_pkce()
        params = {
            "client_id":             config.KICK_CLIENT_ID,
            "redirect_uri":          config.KICK_REDIRECT_URI,
            "response_type":         "code",
            "scope":                 SCOPES,
            "state":                 state,
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    async def handle_callback(self, code: str) -> bool:
        if not self._verifier:
            logger.error("No PKCE verifier – start the auth flow first")
            return False
        data = {
            "grant_type":    "authorization_code",
            "client_id":     config.KICK_CLIENT_ID,
            "client_secret": config.KICK_CLIENT_SECRET,
            "redirect_uri":  config.KICK_REDIRECT_URI,
            "code":          code,
            "code_verifier": self._verifier,
        }
        return await self._fetch_tokens(data)

    async def get_access_token(self) -> str | None:
        if not self._tokens:
            logger.warning("No Kick tokens stored – run the OAuth flow first")
            return None
        if self._is_expired():
            await self._refresh()
        return self._tokens.get("access_token")

    def is_authorized(self) -> bool:
        return bool(self._tokens.get("access_token"))

    def _is_expired(self) -> bool:
        expires_at = self._tokens.get("expires_at", 0)
        return time.time() >= expires_at - 60   # 60 s safety margin

    async def _refresh(self):
        async with self._refresh_lock:
            if not self._is_expired():
                return
            refresh_token = self._tokens.get("refresh_token")
            if not refresh_token:
                logger.error("No refresh token available")
                return
            data = {
                "grant_type":    "refresh_token",
                "client_id":     config.KICK_CLIENT_ID,
                "client_secret": config.KICK_CLIENT_SECRET,
                "refresh_token": refresh_token,
            }
            await self._fetch_tokens(data)

    async def _fetch_tokens(self, data: dict) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    TOKEN_URL, data=data,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    body = await r.json(content_type=None)
                    if r.status != 200 or "access_token" not in body:
                        logger.error(f"Token request failed: {r.status} {body}")
                        return False
                    self._tokens = {
                        "access_token":  body["access_token"],
                        "refresh_token": body.get("refresh_token", self._tokens.get("refresh_token", "")),
                        "expires_at":    time.time() + body.get("expires_in", 3600),
                    }
                    _save_tokens(self._tokens)
                    self._verifier = None
                    return True
        except Exception as e:
            logger.error(f"Token fetch error: {e}")
            return False
