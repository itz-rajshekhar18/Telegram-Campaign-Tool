from contextlib import asynccontextmanager
import csv
import io
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
import uvicorn

from app.config import HOST, PORT, RATE_PROFILES
from app.database import init_db, get_session
from app.models import Campaign, Lead, Account, Report, Message, Group, utcnow
from app.telegram_manager import telegram_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await telegram_manager.disconnect_all()


app = FastAPI(title="Telegram Campaign Tool", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


async def startup():
    """Backwards-compatible startup helper."""
    await init_db()


class CampaignCreate(BaseModel):
    name: str
    objective: str
    info_bank: str
    target_keywords: str
    rate_profile: str = "balanced"
    account_id: Optional[int] = None


@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("templates/dashboard.html")


@app.get("/miniapp", response_class=HTMLResponse)
async def miniapp():
    return FileResponse("static/miniapp.html")


@app.post("/api/campaigns")
async def create_campaign(data: CampaignCreate, session=Depends(get_session)):
    payload = data.model_dump()
    if payload["rate_profile"] not in RATE_PROFILES:
        payload["rate_profile"] = "balanced"
    campaign = Campaign(**payload)
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return {"id": campaign.id, "status": "created"}


@app.get("/api/campaigns")
async def list_campaigns(session=Depends(get_session)):
    result = await session.execute(select(Campaign).order_by(Campaign.id.desc()))
    campaigns = result.scalars().all()
    items = []
    for c in campaigns:
        profile_cfg = telegram_manager.get_profile_config(c.rate_profile)
        usage = await telegram_manager.get_daily_usage(c.id)
        items.append({
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "rate_profile": c.rate_profile,
            "objective": c.objective,
            "target_keywords": c.target_keywords,
            "account_id": c.account_id,
            "daily_usage": usage,
            "daily_caps": {
                "daily_joins": profile_cfg["daily_joins"],
                "daily_messages": profile_cfg["daily_messages"],
            },
            "worker": telegram_manager.get_worker_status(c.id),
        })
    return items


@app.post("/api/campaigns/{campaign_id}/start")
async def start_campaign(
    campaign_id: int,
    session_name: str = "default",
    session=Depends(get_session),
):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    campaign.status = "running"
    await session.commit()
    telegram_manager.start_campaign(campaign_id, session_name=session_name)
    return {"status": "started", "message": "Campaign worker launched"}


@app.post("/api/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int, session=Depends(get_session)):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    campaign.status = "paused"
    await session.commit()
    await telegram_manager.pause_campaign(campaign_id)
    return {"status": "paused"}


@app.post("/api/campaigns/{campaign_id}/stop")
async def stop_campaign(campaign_id: int, session=Depends(get_session)):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    campaign.status = "stopped"
    await session.commit()
    await telegram_manager.stop_campaign(campaign_id)
    return {"status": "stopped"}


@app.get("/api/campaigns/{campaign_id}/usage")
async def campaign_usage(campaign_id: int, session=Depends(get_session)):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    usage = await telegram_manager.get_daily_usage(campaign_id)
    can_join, joins_used, joins_cap = await telegram_manager.can_join_group(
        campaign_id, campaign.rate_profile
    )
    can_send, msgs_used, msgs_cap = await telegram_manager.can_send_message(
        campaign_id, campaign.rate_profile
    )
    return {
        "campaign_id": campaign_id,
        "rate_profile": campaign.rate_profile,
        "usage": usage,
        "caps": {
            "daily_joins": joins_cap,
            "daily_messages": msgs_cap,
            "can_join_more_today": can_join,
            "can_send_more_today": can_send,
            "joins_remaining": max(0, joins_cap - joins_used),
            "messages_remaining": max(0, msgs_cap - msgs_used),
        },
        "worker": telegram_manager.get_worker_status(campaign_id),
    }


@app.get("/api/leads")
async def list_leads(campaign_id: Optional[int] = None, session=Depends(get_session)):
    query = select(Lead)
    if campaign_id:
        query = query.where(Lead.campaign_id == campaign_id)
    result = await session.execute(query.order_by(Lead.created_at.desc()))
    leads = result.scalars().all()
    return [
        {
            "id": l.id,
            "telegram_user_id": l.telegram_user_id,
            "username": l.username,
            "first_name": l.first_name,
            "score": l.score,
            "status": l.status,
            "language": l.language,
            "notes": l.notes,
            "campaign_id": l.campaign_id,
            "last_message_at": l.last_message_at.isoformat() if l.last_message_at else None,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in leads
    ]


@app.get("/api/leads/export")
async def export_leads(campaign_id: Optional[int] = None, session=Depends(get_session)):
    query = select(Lead)
    if campaign_id:
        query = query.where(Lead.campaign_id == campaign_id)
    result = await session.execute(query)
    leads = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Username", "First Name", "Score", "Status", "Language", "Notes", "Created"])
    for l in leads:
        writer.writerow([
            l.id, l.username or "", l.first_name or "", l.score, l.status,
            l.language, l.notes or "", l.created_at
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


@app.get("/api/logs")
async def get_logs(limit: int = 50):
    logs = list(telegram_manager.recent_logs)[-max(1, min(limit, 200)):]
    return {"logs": logs}


@app.get("/api/status")
async def status():
    connected_clients = [
        name for name, client in telegram_manager.clients.items()
        if client and client.is_connected()
    ]
    flood_waits = {
        name: round(telegram_manager.get_flood_wait_remaining(name), 1)
        for name in list(telegram_manager.flood_wait_until.keys())
        if telegram_manager.get_flood_wait_remaining(name) > 0
    }
    return {
        "status": "running",
        "accounts_connected": len(connected_clients),
        "connected_sessions": connected_clients,
        "active_campaigns": len([
            t for t in telegram_manager.running_tasks.values() if not t.done()
        ]),
        "flood_waits": flood_waits,
        "workers": telegram_manager.get_worker_status(),
        "time": utcnow().isoformat(),
    }


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
