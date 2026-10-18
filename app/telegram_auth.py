"""Validation for Telegram Mini App initData and local development identity."""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, Request

from app.config import APP_ENV, TELEGRAM_BOT_TOKEN


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    """Verify Telegram's HMAC signature and return the authenticated user."""
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate field")
        fields = dict(pairs)
        supplied_hash = fields.pop("hash")
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid Telegram Mini App data")

    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise HTTPException(401, "Telegram user identity is missing")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_hash):
        raise HTTPException(401, "Telegram authentication signature is invalid")
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > max_age_seconds:
        raise HTTPException(401, "Telegram authentication has expired; reopen the Mini App")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash):
        raise HTTPException(401, "Telegram authentication signature is invalid")
    return user


async def authenticated_user(request: Request, init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")) -> dict:
    if (APP_ENV == "development" and not init_data and request.client
            and request.client.host in {"127.0.0.1", "::1"}):
        return {"id": 0, "first_name": "Local developer", "is_local": True}
    if TELEGRAM_BOT_TOKEN:
        if not init_data:
            raise HTTPException(401, "Open this app from Telegram to continue")
        return validate_init_data(init_data, TELEGRAM_BOT_TOKEN)
    if APP_ENV != "development":
        raise HTTPException(503, "Telegram Mini App authentication is not configured")
    raise HTTPException(401, "Local development access requires a loopback connection")
