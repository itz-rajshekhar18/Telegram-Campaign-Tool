"""Campaign audience, inbox and persistent delivery queue for a single bot owner."""
import asyncio
import csv
import io
import os
import secrets
from typing import Literal
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func

from app.config import TELEGRAM_BOT_TOKEN, APP_ENV, TELEGRAM_API_BASE_URL, SEND_INTERVAL_SECONDS
from app.database import async_session
from app.models import (Campaign, CampaignVariant, Subscriber, CampaignLink,
                        Broadcast, Delivery, InboxMessage, BotCursor, MetricSnapshot)
from app.telegram_auth import authenticated_user

router = APIRouter(prefix="/api/studio")
sync_lock = asyncio.Lock()
last_sync_error = ""


async def owner(user=Depends(authenticated_user)):
    configured = (os.getenv("CHAT_ID") or "").strip()
    if user.get("is_local"):
        return 0
    if not configured.isdigit() or user["id"] != int(configured):
        raise HTTPException(403, "Campaign management is available only to the configured bot owner.")
    # The local console and the owner's Mini App share one workspace.
    return 0


async def campaign_for(session, campaign_id, owner_id):
    campaign = await session.get(Campaign, campaign_id)
    if not campaign or campaign.telegram_user_id != owner_id:
        raise HTTPException(404, "Campaign not found")
    return campaign


class TelegramError(Exception):
    def __init__(self, code, description, retry_after=0):
        self.code, self.description, self.retry_after = code, description, retry_after


async def telegram(method, payload=None):
    if not TELEGRAM_BOT_TOKEN:
        raise TelegramError(503, "Bot token is not configured")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE_URL}/bot{TELEGRAM_BOT_TOKEN}/{method}", json=payload or {})
        data = response.json()
    except (httpx.RequestError, ValueError):
        raise TelegramError(0, "Telegram connection interrupted; delivery may be uncertain") from None
    if not data.get("ok"):
        description = str(data.get("description", "Telegram rejected the request")).replace(TELEGRAM_BOT_TOKEN, "[redacted]")
        raise TelegramError(data.get("error_code", response.status_code), description,
                            data.get("parameters", {}).get("retry_after", 0))
    return data["result"]


def contact_json(row):
    return {k: getattr(row, k) for k in ("id", "campaign_id", "chat_id", "name", "username", "active", "consent_source", "notes", "stage", "created_at")}


@router.get("/connection")
async def connection(owner_id=Depends(owner)):
    try:
        bot = await telegram("getMe")
        webhook = await telegram("getWebhookInfo")
        return {"connected": True, "username": bot.get("username"), "name": bot.get("first_name"),
                "webhook_enabled": bool(webhook.get("url")), "environment": APP_ENV,
                "default_chat_id": (os.getenv("CHAT_ID") or ""), "sync_error": last_sync_error}
    except TelegramError as error:
        return {"connected": False, "detail": error.description, "environment": APP_ENV}


@router.get("/overview")
async def overview(owner_id=Depends(owner)):
    async with async_session() as s:
        ids = select(Campaign.id).where(Campaign.telegram_user_id == owner_id)
        campaigns = await s.scalar(select(func.count()).select_from(Campaign).where(Campaign.telegram_user_id == owner_id))
        contacts = await s.scalar(select(func.count()).select_from(Subscriber).where(Subscriber.campaign_id.in_(ids), Subscriber.active.is_(True)))
        jobs = select(Broadcast.id).where(Broadcast.campaign_id.in_(ids))
        sent = await s.scalar(select(func.count()).select_from(Delivery).where(Delivery.broadcast_id.in_(jobs), Delivery.status == "sent"))
        queued = await s.scalar(select(func.count()).select_from(Broadcast).where(Broadcast.campaign_id.in_(ids), Broadcast.status.in_(["queued", "running", "paused"])))
        replies = await s.scalar(select(func.count()).select_from(InboxMessage).where(InboxMessage.campaign_id.in_(ids), InboxMessage.direction == "in"))
        return {"campaigns": campaigns, "subscribers": contacts, "sent": sent, "queued": queued, "replies": replies, "sync_error": last_sync_error}


@router.get("/campaigns/{campaign_id}/audience")
async def audience(campaign_id: int, owner_id=Depends(owner)):
    async with async_session() as s:
        await campaign_for(s, campaign_id, owner_id)
        rows = (await s.scalars(select(Subscriber).where(Subscriber.campaign_id == campaign_id).order_by(Subscriber.created_at.desc()))).all()
        link = await s.get(CampaignLink, campaign_id)
        if not link:
            link = CampaignLink(campaign_id=campaign_id, token=secrets.token_urlsafe(18))
            s.add(link)
            await s.commit()
        return {"subscribers": [contact_json(r) for r in rows], "join_code": link.token}


class ContactInput(BaseModel):
    chat_id: int = Field(gt=0)
    name: str = Field(default="", max_length=120)
    consent_confirmed: bool


@router.post("/campaigns/{campaign_id}/audience")
async def add_contact(campaign_id: int, data: ContactInput, owner_id=Depends(owner)):
    if not data.consent_confirmed:
        raise HTTPException(422, "Confirm the contact explicitly agreed to receive this campaign.")
    async with async_session() as s:
        await campaign_for(s, campaign_id, owner_id)
        try:
            chat = await telegram("getChat", {"chat_id": data.chat_id})
        except TelegramError as error:
            raise HTTPException(422, error.description) from None
        if chat.get("type") != "private":
            raise HTTPException(422, "Enter a private chat ID.")
        row = await s.scalar(select(Subscriber).where(Subscriber.campaign_id == campaign_id, Subscriber.chat_id == data.chat_id))
        if row and not row.active:
            raise HTTPException(409, "This contact opted out. They must subscribe again using the campaign join link.")
        if row:
            raise HTTPException(409, "This contact is already in this campaign.")
        row = Subscriber(campaign_id=campaign_id, chat_id=data.chat_id, name=data.name or chat.get("first_name", ""),
                         username=chat.get("username", ""), consent_source="Owner confirmed explicit opt-in")
        s.add(row)
        await s.commit()
        return contact_json(row)


class ContactEdit(BaseModel):
    notes: str = Field(default="", max_length=4000)
    stage: str = Field(pattern="^(new|warm|hot|converted|closed)$")


@router.patch("/audience/{contact_id}")
async def edit_contact(contact_id: int, data: ContactEdit, owner_id=Depends(owner)):
    async with async_session() as s:
        row = await s.get(Subscriber, contact_id)
        if not row:
            raise HTTPException(404, "Contact not found")
        await campaign_for(s, row.campaign_id, owner_id)
        row.notes, row.stage = data.notes, data.stage
        await s.commit()
        return contact_json(row)


@router.delete("/audience/{contact_id}")
async def unsubscribe(contact_id: int, owner_id=Depends(owner)):
    async with async_session() as s:
        row = await s.get(Subscriber, contact_id)
        if not row:
            raise HTTPException(404, "Contact not found")
        await campaign_for(s, row.campaign_id, owner_id)
        row.active = False
        await s.commit()
        return {"status": "unsubscribed"}


async def sync_updates():
    """Persist each page and its offset together before acknowledging it to Telegram."""
    async with sync_lock:
        total = 0
        for _ in range(10):
            async with async_session() as s:
                cursor = await s.get(BotCursor, 1)
                if not cursor:
                    cursor = BotCursor(id=1, offset=0)
                    s.add(cursor)
                updates = await telegram("getUpdates", {"offset": cursor.offset, "limit": 100, "timeout": 0, "allowed_updates": ["message"]})
                for item in updates:
                    message = item.get("message", {})
                    chat = message.get("chat", {})
                    text = message.get("text", "")
                    chat_id = chat.get("id")
                    if chat.get("type") == "private" and text:
                        command = text.split()[0].split("@")[0].lower()
                        if command == "/start" and len(text.split()) == 2:
                            link = await s.scalar(select(CampaignLink).where(CampaignLink.token == text.split()[1]))
                            if link:
                                sub = await s.scalar(select(Subscriber).where(Subscriber.campaign_id == link.campaign_id, Subscriber.chat_id == chat_id))
                                if not sub:
                                    s.add(Subscriber(campaign_id=link.campaign_id, chat_id=chat_id, name=chat.get("first_name", ""), username=chat.get("username", ""), consent_source="Campaign subscribe link /start"))
                                else:
                                    sub.active = True
                                    sub.consent_source = "Campaign subscribe link /start"
                        elif command in {"/stop", "/unsubscribe", "stop"}:
                            await s.execute(update(Subscriber).where(Subscriber.chat_id == chat_id).values(active=False))
                        elif not text.startswith("/"):
                            # Attribute a reply to the most recent campaign delivery to this chat.
                            sub = await s.scalar(select(Subscriber).join(Delivery, Delivery.subscriber_id == Subscriber.id).where(Subscriber.chat_id == chat_id, Delivery.status == "sent").order_by(Delivery.sent_at.desc()).limit(1))
                            if sub and not await s.scalar(select(InboxMessage.id).where(InboxMessage.update_id == item["update_id"])):
                                s.add(InboxMessage(update_id=item["update_id"], campaign_id=sub.campaign_id, subscriber_id=sub.id, text=text))
                    cursor.offset = max(cursor.offset or 0, item["update_id"] + 1)
                    total += 1
                await s.commit()
                if len(updates) < 100:
                    return total
        # Do not send until all pending STOP messages have been processed.
        raise TelegramError(503, "More updates remain. Sync again before launching delivery.")


@router.post("/sync")
async def sync(owner_id=Depends(owner)):
    global last_sync_error
    try:
        count = await sync_updates()
        last_sync_error = ""
        return {"processed": count}
    except TelegramError as error:
        last_sync_error = error.description
        raise HTTPException(502, error.description) from None


class LaunchInput(BaseModel):
    variant_id: int
    scheduled_at: datetime | None = None
    confirmed: bool


@router.post("/campaigns/{campaign_id}/launch")
async def launch(campaign_id: int, data: LaunchInput, owner_id=Depends(owner)):
    if not data.confirmed:
        raise HTTPException(422, "Confirm this campaign send.")
    try:
        await sync_updates()
    except TelegramError as error:
        raise HTTPException(502, error.description) from None
    async with async_session() as s:
        c = await campaign_for(s, campaign_id, owner_id)
        if not c.consent_confirmed:
            raise HTTPException(422, "Campaign consent is required.")
        if c.deadline < datetime.now().date().isoformat():
            raise HTTPException(422, "Update the campaign deadline before launching.")
        existing = await s.scalar(select(Broadcast.id).where(Broadcast.campaign_id == campaign_id, Broadcast.status.in_(["queued", "running", "paused"])))
        if existing:
            raise HTTPException(409, "This campaign already has an unfinished send. Complete or cancel it first.")
        variant = await s.get(CampaignVariant, data.variant_id)
        if not variant or variant.campaign_id != campaign_id:
            raise HTTPException(404, "Draft not found")
        if not all(getattr(variant, key).strip() for key in ("attention", "interest", "desire", "action")):
            raise HTTPException(422, "Complete all four message sections before sending.")
        text = variant.copy
        if "stop" not in text.lower():
            text += "\n\nTo stop these updates, reply STOP."
        if len(text.encode("utf-16-le")) // 2 > 4096:
            raise HTTPException(422, "The combined message exceeds Telegram's 4096-character limit.")
        subscribers = (await s.scalars(select(Subscriber).where(Subscriber.campaign_id == campaign_id, Subscriber.active.is_(True)))).all()
        if not subscribers:
            raise HTTPException(422, "Add at least one opted-in contact first.")
        scheduled = data.scheduled_at or datetime.now(timezone.utc)
        if scheduled.tzinfo is None:
            raise HTTPException(422, "Schedule must include a timezone.")
        job = Broadcast(campaign_id=campaign_id, variant_id=variant.id, text=text, scheduled_at=scheduled.astimezone(timezone.utc).replace(tzinfo=None))
        s.add(job)
        await s.flush()
        s.add_all([Delivery(broadcast_id=job.id, subscriber_id=sub.id) for sub in subscribers])
        c.status = "scheduled" if job.scheduled_at > datetime.utcnow() else "running"
        await s.commit()
        return {"id": job.id, "recipients": len(subscribers), "status": job.status}


@router.get("/campaigns/{campaign_id}/deliveries")
async def deliveries(campaign_id: int, owner_id=Depends(owner)):
    async with async_session() as s:
        await campaign_for(s, campaign_id, owner_id)
        jobs = (await s.scalars(select(Broadcast).where(Broadcast.campaign_id == campaign_id).order_by(Broadcast.id.desc()))).all()
        result = []
        for job in jobs:
            rows = (await s.execute(select(Delivery, Subscriber.name).join(Subscriber).where(Delivery.broadcast_id == job.id).order_by(Delivery.id))).all()
            counts = {}
            for d, name in rows:
                counts[d.status] = counts.get(d.status, 0) + 1
            result.append({"id": job.id, "variant_id": job.variant_id, "status": job.status, "scheduled_at": job.scheduled_at,
                           "counts": counts, "total": len(rows), "text": job.text,
                           "deliveries": [{"id": d.id, "name": name, "status": d.status, "error": d.error, "sent_at": d.sent_at} for d, name in rows]})
        return result


@router.post("/broadcasts/{job_id}/{action}")
async def job_action(job_id: int, action: str, owner_id=Depends(owner)):
    if action not in {"pause", "resume", "cancel"}:
        raise HTTPException(404, "Action not found")
    async with async_session() as s:
        job = await s.get(Broadcast, job_id)
        if not job:
            raise HTTPException(404, "Send not found")
        c = await campaign_for(s, job.campaign_id, owner_id)
        if job.status in {"completed", "cancelled"}:
            raise HTTPException(409, "This send has finished.")
        job.status = {"pause": "paused", "resume": "queued", "cancel": "cancelled"}[action]
        c.status = {"pause": "paused", "resume": "running", "cancel": "stopped"}[action]
        if action == "cancel":
            await s.execute(update(Delivery).where(Delivery.broadcast_id == job_id, Delivery.status == "pending").values(status="cancelled"))
        await s.commit()
        return {"status": job.status}


@router.get("/inbox")
async def inbox(owner_id=Depends(owner)):
    async with async_session() as s:
        rows = (await s.execute(select(InboxMessage, Subscriber, Campaign.name).join(Subscriber, Subscriber.id == InboxMessage.subscriber_id).join(Campaign, Campaign.id == InboxMessage.campaign_id).where(Campaign.telegram_user_id == owner_id).order_by(InboxMessage.id.desc()).limit(300))).all()
        return [{"id": m.id, "contact_id": sub.id, "name": sub.name, "campaign": name, "direction": m.direction, "text": m.text, "created_at": m.created_at, "active": sub.active} for m, sub, name in rows]


class ReplyInput(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


@router.post("/audience/{contact_id}/reply")
async def reply(contact_id: int, data: ReplyInput, owner_id=Depends(owner)):
    try:
        await sync_updates()
    except TelegramError as error:
        raise HTTPException(502, error.description) from None
    async with async_session() as s:
        sub = await s.get(Subscriber, contact_id)
        if not sub:
            raise HTTPException(404, "Contact not found")
        await campaign_for(s, sub.campaign_id, owner_id)
        if not sub.active or not data.text.strip():
            raise HTTPException(422, "Contact is unsubscribed or reply is empty.")
        try:
            await telegram("sendMessage", {"chat_id": sub.chat_id, "text": data.text.strip()})
        except TelegramError as error:
            raise HTTPException(502, error.description) from None
        s.add(InboxMessage(campaign_id=sub.campaign_id, subscriber_id=sub.id, direction="out", text=data.text.strip()))
        await s.commit()
        return {"status": "sent"}


@router.get("/campaigns/{campaign_id}/export")
async def export(campaign_id: int, kind: Literal["contacts", "metrics", "deliveries"] = "contacts", owner_id=Depends(owner)):
    async with async_session() as s:
        await campaign_for(s, campaign_id, owner_id)
        output = io.StringIO()
        writer = csv.writer(output)
        if kind == "contacts":
            keys = ["name", "username", "chat_id", "active", "stage", "consent_source", "notes"]
            rows = (await s.scalars(select(Subscriber).where(Subscriber.campaign_id == campaign_id))).all()
        elif kind == "metrics":
            keys = ["variant_id", "period", "delivered", "clicks", "conversions", "replies", "signups", "opt_outs", "complaints", "spend"]
            rows = (await s.scalars(select(MetricSnapshot).where(MetricSnapshot.campaign_id == campaign_id))).all()
        else:
            keys = ["broadcast_id", "subscriber_id", "status", "message_id", "error", "sent_at"]
            rows = (await s.scalars(select(Delivery).join(Broadcast).where(Broadcast.campaign_id == campaign_id))).all()
        writer.writerow(keys)
        for row in rows:
            values = [str(getattr(row, key)) if getattr(row, key) is not None else "" for key in keys]
            writer.writerow(["'" + v if v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
        return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="campaign-{campaign_id}-{kind}.csv"'})


async def worker():
    """Single-process local queue. Never replay an uncertain send after a crash."""
    global last_sync_error
    async with async_session() as s:
        await s.execute(update(Delivery).where(Delivery.status == "sending").values(status="unknown", error="Server restarted during send; check Telegram before sending again"))
        await s.commit()
    counter = 0
    while True:
        try:
            if TELEGRAM_BOT_TOKEN and counter % 10 == 0:
                try:
                    await sync_updates()
                    last_sync_error = ""
                except TelegramError as error:
                    last_sync_error = error.description
            counter += 1
            async with async_session() as s:
                job = await s.scalar(select(Broadcast).where(Broadcast.status.in_(["queued", "running"]), Broadcast.scheduled_at <= datetime.utcnow()).order_by(Broadcast.id).limit(1))
                if job and not last_sync_error:
                    d = await s.scalar(select(Delivery).where(Delivery.broadcast_id == job.id, Delivery.status == "pending").order_by(Delivery.id).limit(1))
                    if not d:
                        job.status = "completed"
                        c = await s.get(Campaign, job.campaign_id)
                        c.status = "completed"
                        await s.commit()
                    else:
                        sub = await s.get(Subscriber, d.subscriber_id)
                        if not sub.active:
                            d.status = "skipped"
                            d.error = "Contact unsubscribed"
                            await s.commit()
                        else:
                            job.status = "running"
                            d.status = "sending"
                            await s.commit()
                            try:
                                result = await telegram("sendMessage", {"chat_id": sub.chat_id, "text": job.text})
                                d.status, d.message_id, d.sent_at = "sent", result["message_id"], datetime.utcnow()
                            except TelegramError as error:
                                if error.retry_after:
                                    d.status = "pending"
                                    job.scheduled_at = datetime.utcnow() + timedelta(seconds=error.retry_after + 1)
                                else:
                                    d.status = "unknown" if error.code == 0 else "failed"
                                    d.error = error.description
                                    if error.code == 403:
                                        sub.active = False
                            await s.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep credentials and request URLs out of logs and client responses.
            last_sync_error = "Queue paused by a storage or processing error. Restart the app and inspect delivery history."
        await asyncio.sleep(SEND_INTERVAL_SECONDS)
