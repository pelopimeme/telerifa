"""Telegram Mini App authentication.

The Mini App sends the raw `initData` string as:   Authorization: tma <initData>
We verify Telegram's HMAC (keyed with the bot token) so the caller's identity
can't be forged, and reject stale launches (replay protection).
Docs: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from .config import settings

MAX_AGE_SECONDS = 24 * 3600


@dataclass
class TgUser:
    id: int
    username: str | None


def validate_init_data(init_data: str, bot_token: str, max_age: int = MAX_AGE_SECONDS) -> dict:
    """Return the decoded `user` object, or raise ValueError."""
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("missing hash")
    # Every field except `hash`, sorted, "key=value" joined by \n.
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("bad signature")
    if time.time() - int(pairs.get("auth_date", "0")) > max_age:
        raise ValueError("init data expired")
    return json.loads(pairs["user"])


async def current_user(authorization: str = Header(default="")) -> TgUser:
    """FastAPI dependency: 401 unless the request carries valid Telegram initData."""
    if not authorization.startswith("tma ") or not settings.telegram_bot_token:
        raise HTTPException(401, "Missing Telegram credentials")
    try:
        user = validate_init_data(authorization[4:], settings.telegram_bot_token)
        return TgUser(id=int(user["id"]), username=user.get("username"))
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid Telegram credentials")
