from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn
import io
import csv
from datetime import datetime
from sqlalchemy import select
from app.database import init_db, get_session
from app.models import Campaign, Lead, Account, Report, Message
from app.telegram_manager import telegram_manager
from app.config import HOST, PORT

app = FastAPI(title="Telegram Campaign Tool")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
async def startup():
    await init_db()

class CampaignCreate(BaseModel):
    name: str
    objective: str
    info_bank: str
    target_keywords: str
    rate_profile: str = "balanced"

@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("templates/dashboard.html")

@app.get("/miniapp", response_class=HTMLResponse)
async def miniapp():
    return FileResponse("static/miniapp.html")

@app.post("/api/campaigns")
async def create_campaign(data: CampaignCreate, session=Depends(get_session)):
    campaign = Campaign(**data.model_dump())
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return {"id": campaign.id, "status": "created"}

@app.get("/api/campaigns")
async def list_campaigns(session=Depends(get_session)):
    result = await session.execute(select(Campaign))
    campaigns = result.scalars().all()
    return [
        {"id": c.id, "name": c.name, "status": c.status, "rate_profile": c.rate_profile, "objective": c.objective}
        for c in campaigns
    ]

@app.post("/api/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: int, session=Depends(get_session)):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    campaign.status = "running"
    await session.commit()
    # Start background worker (uses default session for now)
    telegram_manager.start_campaign(campaign_id, session_name="default")
    return {"status": "started", "message": "Campaign worker launched"}

@app.post("/api/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int, session=Depends(get_session)):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if campaign:
        campaign.status = "paused"
        await session.commit()
    return {"status": "paused"}

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
            "username": l.username,
            "first_name": l.first_name,
            "score": l.score,
            "status": l.status,
            "language": l.language,
            "notes": l.notes,
            "created_at": l.created_at.isoformat() if l.created_at else None
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
        headers={"Content-Disposition": "attachment; filename=leads.csv"}
    )

@app.get("/api/status")
async def status():
    return {
        "status": "running",
        "accounts_connected": len(telegram_manager.clients),
        "active_campaigns": len([t for t in telegram_manager.running_tasks.values() if not t.done()]),
        "time": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
