"""Owner-only configuration and small, explicit direct sends."""
import asyncio
import os
import re
import tempfile

import httpx
from dotenv import dotenv_values
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select, func

from app.config import PROJECT_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_API_BASE_URL, SEND_INTERVAL_SECONDS, DIRECT_SEND_MAX_RECIPIENTS
from app.database import async_session
from app.models import Campaign, DirectSendRecord
from app.studio import owner

router = APIRouter(prefix="/api/studio")
send_lock = asyncio.Lock()
settings_lock = asyncio.Lock()
ENV_PATH = PROJECT_DIR / ".env"
TOKEN_PATTERN = r"[0-9]+:[A-Za-z0-9_-]{20,}"


async def recover_direct_history():
    """A direct request cannot resume after restart; do not imply queued work remains."""
    async with async_session() as session:
        rows = (await session.scalars(select(DirectSendRecord))).all()
        for row in rows:
            if any(item.get("status") == "pending" for item in row.results):
                row.results = [{**item, "status": "skipped", "detail": "Server stopped before this chat was attempted."}
                               if item.get("status") == "pending" else item for item in row.results]
        await session.commit()


class Credentials(BaseModel):
    bot_token: SecretStr = Field(default=SecretStr(""), max_length=200)

    @field_validator("bot_token")
    @classmethod
    def token_format(cls, value):
        raw = value.get_secret_value()
        if raw and not re.fullmatch(TOKEN_PATTERN, raw):
            raise ValueError("Enter a valid BotFather token without spaces")
        return value


class SettingsInput(Credentials):
    owner_chat_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")


class DirectInput(Credentials):
    chat_ids: list[str] = Field(min_length=1, max_length=DIRECT_SEND_MAX_RECIPIENTS)
    text: str = Field(min_length=1, max_length=4096)
    confirmed: bool

    @field_validator("chat_ids")
    @classmethod
    def valid_chats(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if not re.fullmatch(r"-?[1-9][0-9]{0,15}|@[A-Za-z][A-Za-z0-9_]{4,31}", value):
                raise ValueError("Use numeric chat IDs or public @channel usernames")
            if value.lower() not in {v.lower() for v in result}:
                result.append(value)
        return result

    @field_validator("text")
    @classmethod
    def valid_text(cls, value):
        if not value.strip():
            raise ValueError("Enter a message")
        return value


async def bot_call(token, method, payload=None):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}", json=payload or {})
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
    except (httpx.RequestError, ValueError):
        return {"status": "unknown", "detail": "Connection interrupted. Check Telegram before resending."}
    if response.is_error or not data.get("ok"):
        retry_after = data.get("parameters", {}).get("retry_after", 0)
        # Never echo Telegram descriptions, which may include submitted credentials.
        return {"status": "failed", "detail": "Telegram rejected the request. Check the token, chat ID and bot permissions.",
                "retry_after": retry_after, "code": data.get("error_code", response.status_code)}
    return {"status": "sent", "result": data["result"]}


@router.get("/settings")
async def settings(response: Response, owner_id=Depends(owner)):
    response.headers["Cache-Control"] = "no-store"
    saved = dotenv_values(ENV_PATH)
    saved_token = saved.get("TELEGRAM_BOT_TOKEN") or ""
    return {"token_saved": bool(saved_token), "token_active": bool(TELEGRAM_BOT_TOKEN),
            "owner_chat_id": saved.get("CHAT_ID") or "",
            "restart_required": saved_token != TELEGRAM_BOT_TOKEN or (saved.get("CHAT_ID") or "") != (os.getenv("CHAT_ID") or "")}


@router.put("/settings")
async def save_settings(data: SettingsInput, owner_id=Depends(owner)):
    async with settings_lock:
        saved = dotenv_values(ENV_PATH)
        token = data.bot_token.get_secret_value() or saved.get("TELEGRAM_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
        if not token:
            raise HTTPException(422, "Enter a bot token")
        verified = await bot_call(token, "getMe")
        if verified["status"] != "sent":
            raise HTTPException(422, "Could not verify this bot token. Settings were not saved.")
        # Existing subscriptions and polling offsets belong to the original bot.
        previous = TELEGRAM_BOT_TOKEN or saved.get("TELEGRAM_BOT_TOKEN") or ""
        if previous and token.split(":")[0] != previous.split(":")[0]:
            async with async_session() as session:
                count = await session.scalar(select(func.count()).select_from(Campaign))
            if count:
                raise HTTPException(409, "Existing campaigns belong to the current bot. Use the direct sender for another bot, or use a separate workspace for its campaigns.")
        replacements = {"TELEGRAM_BOT_TOKEN": token, "CHAT_ID": data.owner_chat_id}
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
        output = []
        for line in lines:
            match = re.match(r"^\s*(?:export\s+)?(TELEGRAM_BOT_TOKEN|CHAT_ID)\s*=", line)
            if not match:
                output.append(line)
        output.extend(f"{key}='{value}'" for key, value in replacements.items())
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ENV_PATH.parent, prefix=".env-", suffix=".tmp", delete=False) as handle:
                temp_path = handle.name
                handle.write("\n".join(output) + "\n")
            os.replace(temp_path, ENV_PATH)
        except OSError:
            raise HTTPException(500, "Could not save settings to the server .env file") from None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        return {"saved": True, "restart_required": token != TELEGRAM_BOT_TOKEN or data.owner_chat_id != (os.getenv("CHAT_ID") or ""),
                "username": verified["result"].get("username")}


@router.post("/direct-send")
async def direct_send(data: DirectInput, owner_id=Depends(owner)):
    if not data.confirmed:
        raise HTTPException(422, "Confirm that you own these chats or have permission to message them")
    token = data.bot_token.get_secret_value() or TELEGRAM_BOT_TOKEN
    if not token:
        raise HTTPException(422, "Enter a bot token, or configure the workspace bot first")
    if send_lock.locked():
        raise HTTPException(409, "A direct send is already in progress. Wait for its results.")
    async with send_lock:
        results = []
        async with async_session() as session:
            record = DirectSendRecord(owner_id=owner_id, bot_id=token.split(":")[0], text=data.text,
                                      results=[{"chat_id": chat, "status": "pending"} for chat in data.chat_ids])
            session.add(record)
            await session.commit()
            record_id = record.id

        async def save_results(rows):
            async with async_session() as session:
                stored = await session.get(DirectSendRecord, record_id)
                stored.results = rows
                await session.commit()

        stop = False
        for index, chat_id in enumerate(data.chat_ids):
            if stop:
                results.append({"chat_id": chat_id, "status": "skipped", "detail": "Batch stopped after an uncertain delivery, token error or Telegram rate limit."})
                continue
            if index:
                await asyncio.sleep(SEND_INTERVAL_SECONDS)
            await save_results(results + [{"chat_id": chat_id, "status": "unknown", "detail": "Delivery started; check Telegram if interrupted."}] +
                               [{"chat_id": chat, "status": "pending"} for chat in data.chat_ids[index + 1:]])
            result = await bot_call(token, "sendMessage", {"chat_id": chat_id, "text": data.text})
            row = {"chat_id": chat_id, "status": result["status"]}
            if result["status"] == "sent":
                row["message_id"] = result["result"]["message_id"]
            else:
                row["detail"] = result["detail"]
                row["retry_after"] = result.get("retry_after", 0)
            results.append(row)
            await save_results(results + [{"chat_id": chat, "status": "pending"} for chat in data.chat_ids[index + 1:]])
            stop = result["status"] == "unknown" or bool(result.get("retry_after")) or result.get("code") == 401
        await save_results(results)
        return {"id": record_id, "results": results, "sent": sum(r["status"] == "sent" for r in results)}


@router.get("/direct-history")
async def direct_history(owner_id=Depends(owner)):
    async with async_session() as session:
        records = (await session.scalars(select(DirectSendRecord).where(DirectSendRecord.owner_id == owner_id).order_by(DirectSendRecord.id.desc()).limit(50))).all()
        return [{"id": row.id, "bot_id": row.bot_id, "text": row.text, "results": row.results, "created_at": row.created_at} for row in records]
