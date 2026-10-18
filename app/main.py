from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlparse
from typing import Optional, Literal
import httpx
import uvicorn
import io
import csv
import asyncio
from contextlib import suppress, asynccontextmanager
from datetime import date, datetime
from sqlalchemy import select, func
from app.database import init_db, get_session, async_session
from app.models import Campaign, CampaignVariant, MetricSnapshot, Lead, Broadcast
from app.campaign_tools import build_aida_variants, quality_check, optimize_results
from app.config import HOST, PORT, TELEGRAM_BOT_TOKEN, PROJECT_DIR, TELEGRAM_API_BASE_URL
from app.telegram_auth import authenticated_user
from app.studio import router as studio_router, worker, owner
from app.insights import router as insights_router
from app.connection import router as connection_router, recover_direct_history
from app.reports import router as reports_router
from app.workspace import router as workspace_router, workspace_values

@asynccontextmanager
async def lifespan(application):
    await init_db()
    await recover_direct_history()
    task = asyncio.create_task(worker())
    application.state.queue_task = task
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

app = FastAPI(title="Campaign Studio", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=PROJECT_DIR / "static"), name="static")
app.include_router(studio_router)
app.include_router(insights_router)
app.include_router(connection_router)
app.include_router(reports_router)
app.include_router(workspace_router)

@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Pydantic includes rejected input by default; credentials must never be echoed.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
        for item in error.errors()
    ]})

async def campaign_user(user=Depends(authenticated_user)):
    owner_id = await owner(user)
    return {**user, "id": owner_id}

class CampaignCreate(BaseModel):
    category: str = Field(default="general", max_length=80)
    budget: float = Field(default=0, ge=0, le=1000000000, allow_inf_nan=False)
    currency: str = Field(default="INR", pattern="^[A-Z]{3}$")
    landing_page: str = Field(default="", max_length=2000)
    language: str = Field(default="English", max_length=80)
    tone: str = Field(default="friendly", max_length=80)
    tags: str = Field(default="", max_length=500)
    internal_notes: str = Field(default="", max_length=6000)

    @field_validator("landing_page")
    @classmethod
    def validate_landing_page(cls, value):
        value = value.strip()
        parsed = urlparse(value)
        if value and (parsed.scheme not in {"http", "https"} or not parsed.netloc):
            raise ValueError("Enter a valid http or https landing page URL")
        return value
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=1200)
    info_bank: str = Field(min_length=1, max_length=6000)
    target_keywords: str = ""
    rate_profile: str = "balanced"
    audience: str = Field(min_length=3, max_length=500)
    audience_size: int = Field(gt=0, le=100000000)
    product_name: str = Field(min_length=1, max_length=120)
    value_proposition: str = Field(min_length=5, max_length=500)
    proof: str = Field(default="", max_length=500)
    offer: str = Field(default="", max_length=500)
    call_to_action: str = Field(min_length=3, max_length=240)
    primary_metric: Literal["conversions", "clicks", "replies", "signups"]
    target_value: int = Field(gt=0, le=100000000)
    deadline: str = Field(min_length=8, max_length=10)
    consent_confirmed: bool

class VariantUpdate(BaseModel):
    attention: str = Field(min_length=1, max_length=1000)
    interest: str = Field(min_length=1, max_length=1000)
    desire: str = Field(min_length=1, max_length=1000)
    action: str = Field(min_length=1, max_length=1000)

class MetricInput(BaseModel):
    variant_id: int
    period: str
    delivered: int = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: int = Field(ge=0)
    replies: int = Field(ge=0)
    signups: int = Field(ge=0)
    opt_outs: int = Field(ge=0)
    complaints: int = Field(ge=0)
    spend: float = Field(default=0, ge=0)

class TelegramTestSend(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    chat_id: Optional[int] = Field(default=None, gt=0)

def variant_payload(variant):
    return {
        "id": variant.id, "label": variant.label,
        "attention": variant.attention, "interest": variant.interest,
        "desire": variant.desire, "action": variant.action,
        "copy": variant.copy, "score": variant.score,
        "score_details": variant.score_details or {},
        "score_out_of": len(VariantUpdate.model_fields),
    }

async def get_campaign(session, campaign_id, user_id):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.telegram_user_id == user_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return campaign

@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse(PROJECT_DIR / "static" / "miniapp.html")

@app.get("/miniapp", response_class=HTMLResponse)
async def miniapp():
    return FileResponse(PROJECT_DIR / "static" / "miniapp.html")

@app.get("/api/session")
async def session_info(user=Depends(authenticated_user)):
    return {"user": {"id": user["id"], "first_name": user.get("first_name", ""),
                      "username": user.get("username", ""), "is_local": user.get("is_local", False)},
            "telegram_send_configured": bool(TELEGRAM_BOT_TOKEN)}

@app.post("/api/telegram/test-send")
async def send_telegram_test(data: TelegramTestSend, user=Depends(authenticated_user)):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(503, "Telegram sending is not configured. Set TELEGRAM_BOT_TOKEN and restart the app.")
    message = data.text.strip()
    if not message:
        raise HTTPException(422, "Enter a test message.")
    if user.get("is_local"):
        if data.chat_id is None:
            raise HTTPException(422, "Enter your numeric Telegram chat ID for a local browser test.")
        chat_id = data.chat_id
    else:
        chat_id = user["id"]
        if data.chat_id is not None and data.chat_id != chat_id:
            raise HTTPException(403, "A Telegram Mini App test can only be sent to your own Telegram account.")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE_URL}/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": message},
            )
    except httpx.TimeoutException:
        raise HTTPException(504, "Telegram did not respond in time. Try again.")
    except httpx.RequestError:
        raise HTTPException(502, "Could not connect to Telegram. Check the server network and try again.")

    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.is_error or not result.get("ok"):
        raise HTTPException(502, "Telegram could not send the message. Start the bot in your private chat and verify the chat ID.")
    return {"status": "sent", "chat_id": chat_id, "message_id": result["result"]["message_id"]}

@app.post("/api/campaigns")
async def create_campaign(data: CampaignCreate, session=Depends(get_session), user=Depends(campaign_user)):
    if not data.consent_confirmed:
        raise HTTPException(422, "Confirm that this campaign is only for people who explicitly opted in.")
    try:
        date.fromisoformat(data.deadline)
    except ValueError:
        raise HTTPException(422, "Deadline must be a valid date in YYYY-MM-DD format.")
    values = data.model_dump()
    defaults = await workspace_values(user["id"])
    for key in ("category", "currency", "language", "tone"):
        if key not in data.model_fields_set:
            values[key] = defaults["default_" + key]
    campaign = Campaign(**values, telegram_user_id=user["id"])
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return {"id": campaign.id, "status": "draft", "quality": quality_check(campaign)}

@app.put("/api/campaigns/{campaign_id}")
async def update_campaign(campaign_id: int, data: CampaignCreate, session=Depends(get_session), user=Depends(campaign_user)):
    campaign = await get_campaign(session, campaign_id, user["id"])
    if campaign.currency != data.currency:
        recorded = await session.scalar(select(MetricSnapshot.id).where(MetricSnapshot.campaign_id == campaign_id).limit(1))
        if recorded:
            raise HTTPException(409, "Currency cannot change after daily results are recorded. Create a separate campaign for another currency.")
    if not data.consent_confirmed:
        raise HTTPException(422, "Confirm the audience has opted in.")
    try:
        date.fromisoformat(data.deadline)
    except ValueError:
        raise HTTPException(422, "Deadline must be a valid date.")
    for key, value in data.model_dump().items():
        setattr(campaign, key, value)
    await session.commit()
    return {"id": campaign.id, "status": campaign.status}

@app.get("/api/campaigns")
async def list_campaigns(session=Depends(get_session), user=Depends(campaign_user)):
    result = await session.execute(select(Campaign).where(Campaign.telegram_user_id == user["id"]))
    campaigns = result.scalars().all()
    return [
        {"id": c.id, "name": c.name, "status": c.status, "rate_profile": c.rate_profile,
         "objective": c.objective, "audience": c.audience, "primary_metric": c.primary_metric,
         "target_value": c.target_value, "deadline": c.deadline, "product_name": c.product_name,
         "category": c.category, "tags": c.tags, "budget": c.budget, "currency": c.currency}
        for c in campaigns
    ]

@app.get("/api/campaigns/{campaign_id}")
async def campaign_detail(campaign_id: int, session=Depends(get_session), user=Depends(campaign_user)):
    campaign = await get_campaign(session, campaign_id, user["id"])
    result = await session.execute(select(CampaignVariant).where(CampaignVariant.campaign_id == campaign_id).order_by(CampaignVariant.id))
    variants = result.scalars().all()
    return {"id": campaign.id, "name": campaign.name, "objective": campaign.objective,
            "info_bank": campaign.info_bank, "target_keywords": campaign.target_keywords, "rate_profile": campaign.rate_profile,
            "audience": campaign.audience, "audience_size": campaign.audience_size, "product_name": campaign.product_name,
            "value_proposition": campaign.value_proposition, "proof": campaign.proof,
            "offer": campaign.offer, "call_to_action": campaign.call_to_action,
            "primary_metric": campaign.primary_metric, "target_value": campaign.target_value,
            "deadline": campaign.deadline, "consent_confirmed": campaign.consent_confirmed,
            "status": campaign.status,
            **{key: getattr(campaign, key) for key in ("category", "budget", "currency", "landing_page", "language", "tone", "tags", "internal_notes")},
            "variants": [variant_payload(v) for v in variants],
            "quality": quality_check(campaign, variants)}

@app.post("/api/campaigns/{campaign_id}/drafts")
async def generate_campaign_drafts(campaign_id: int, session=Depends(get_session), user=Depends(campaign_user)):
    campaign = await get_campaign(session, campaign_id, user["id"])
    if not campaign.consent_confirmed:
        raise HTTPException(422, "Drafts require an explicit opt-in audience confirmation.")
    existing = await session.execute(select(CampaignVariant).where(CampaignVariant.campaign_id == campaign_id))
    if existing.scalars().first():
        raise HTTPException(409, "Draft variants already exist. Edit them or create a new campaign to test another concept.")
    variants = []
    settings = await workspace_values(user["id"])
    try:
        drafts = build_aida_variants(campaign, settings["draft_templates"])
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    for item in drafts:
        variant = CampaignVariant(campaign_id=campaign_id, **item)
        session.add(variant)
        variants.append(variant)
    await session.flush()
    for variant in variants:
        assessment = quality_check(campaign, [variant])["aida"][0]
        variant.score = assessment["score"]
        variant.score_details = assessment["sections"]
    await session.commit()
    return {"variants": [variant_payload(v) for v in variants], "quality": quality_check(campaign, variants)}

@app.put("/api/campaigns/{campaign_id}/variants/{variant_id}")
async def edit_variant(campaign_id: int, variant_id: int, data: VariantUpdate, session=Depends(get_session), user=Depends(campaign_user)):
    campaign = await get_campaign(session, campaign_id, user["id"])
    result = await session.execute(select(CampaignVariant).where(CampaignVariant.id == variant_id, CampaignVariant.campaign_id == campaign_id))
    variant = result.scalar_one_or_none()
    if not variant:
        raise HTTPException(404, "Draft variant not found")
    for key, value in data.model_dump().items():
        setattr(variant, key, value.strip())
    assessment = quality_check(campaign, [variant])["aida"][0]
    variant.score = assessment["score"]
    variant.score_details = assessment["sections"]
    await session.commit()
    return variant_payload(variant)

@app.post("/api/campaigns/{campaign_id}/metrics")
async def record_campaign_metrics(campaign_id: int, data: MetricInput, session=Depends(get_session), user=Depends(campaign_user)):
    await get_campaign(session, campaign_id, user["id"])
    try:
        date.fromisoformat(data.period)
    except ValueError:
        raise HTTPException(422, "Period must be a valid date in YYYY-MM-DD format.")
    if any(count > data.delivered for count in (data.clicks, data.conversions, data.replies, data.signups, data.opt_outs, data.complaints)):
        raise HTTPException(422, "Clicks, conversions, opt-outs and complaints cannot exceed delivered messages.")
    result = await session.execute(select(CampaignVariant).where(CampaignVariant.id == data.variant_id, CampaignVariant.campaign_id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Draft variant not found for this campaign")
    result = await session.execute(select(MetricSnapshot).where(MetricSnapshot.variant_id == data.variant_id, MetricSnapshot.period == data.period))
    snapshot = result.scalar_one_or_none()
    values = data.model_dump(exclude={"variant_id", "period"})
    if snapshot:
        for key, value in values.items():
            setattr(snapshot, key, value)
    else:
        snapshot = MetricSnapshot(campaign_id=campaign_id, variant_id=data.variant_id, period=data.period, **values)
        session.add(snapshot)
    await session.commit()
    return {"status": "recorded", "period": data.period}

@app.get("/api/campaigns/{campaign_id}/metrics")
async def list_campaign_metrics(campaign_id: int, session=Depends(get_session), user=Depends(campaign_user)):
    await get_campaign(session, campaign_id, user["id"])
    result = await session.execute(
        select(MetricSnapshot)
        .where(MetricSnapshot.campaign_id == campaign_id)
        .order_by(MetricSnapshot.period, MetricSnapshot.variant_id)
    )
    return [
        {
            "variant_id": row.variant_id,
            "period": row.period,
            "delivered": row.delivered,
            "clicks": row.clicks,
            "conversions": row.conversions,
            "replies": row.replies,
            "signups": row.signups,
            "opt_outs": row.opt_outs,
            "complaints": row.complaints,
            "spend": row.spend,
        }
        for row in result.scalars().all()
    ]

@app.get("/api/campaigns/{campaign_id}/optimization")
async def campaign_optimization(campaign_id: int, session=Depends(get_session), user=Depends(campaign_user)):
    campaign = await get_campaign(session, campaign_id, user["id"])
    result = await session.execute(select(CampaignVariant).where(CampaignVariant.campaign_id == campaign_id).order_by(CampaignVariant.id))
    variants = result.scalars().all()
    result = await session.execute(select(MetricSnapshot).where(MetricSnapshot.campaign_id == campaign_id))
    snapshots = result.scalars().all()
    settings = await workspace_values(user["id"])
    result = optimize_results(variants, snapshots, campaign.primary_metric, settings)
    metric_field = campaign.primary_metric if campaign.primary_metric in {"clicks", "conversions", "replies", "signups"} else "conversions"
    achieved = sum(getattr(row, metric_field) for row in snapshots)
    for row in result["variants"]:
        row["goal_count"] = row[metric_field]
        row["goal_rate"] = round(row[metric_field] / row["delivered"] * 100, 2) if row["delivered"] else 0
    result["goal"] = {"metric": metric_field, "target": campaign.target_value, "achieved": achieved,
                      "progress_percent": round(min(achieved / campaign.target_value * 100, 100), 1) if campaign.target_value else 0,
                      "complete": achieved >= campaign.target_value if campaign.target_value else False,
                      "deadline": campaign.deadline}
    return result

@app.post("/api/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: int, session=Depends(get_session), user=Depends(campaign_user)):
    await get_campaign(session, campaign_id, user["id"])
    raise HTTPException(409, "Choose a saved variant and launch from the Delivery tab.")

@app.post("/api/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int, session=Depends(get_session), user=Depends(campaign_user)):
    campaign = await get_campaign(session, campaign_id, user["id"])
    jobs = (await session.scalars(select(Broadcast).where(Broadcast.campaign_id == campaign_id, Broadcast.status.in_(["queued", "running"])))).all()
    for job in jobs:
        job.status = "paused"
    campaign.status = "paused"
    await session.commit()
    return {"status": "paused"}

@app.get("/api/leads")
async def list_leads(campaign_id: Optional[int] = None, session=Depends(get_session), user=Depends(campaign_user)):
    query = select(Lead)
    query = query.join(Campaign, Lead.campaign_id == Campaign.id).where(Campaign.telegram_user_id == user["id"])
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
async def export_leads(campaign_id: Optional[int] = None, session=Depends(get_session), user=Depends(campaign_user)):
    query = select(Lead)
    query = query.join(Campaign, Lead.campaign_id == Campaign.id).where(Campaign.telegram_user_id == user["id"])
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
async def status(owner_id=Depends(owner)):
    async with async_session() as session:
        campaigns = await session.scalar(select(func.count()).select_from(Campaign).where(Campaign.telegram_user_id == owner_id))
        active = await session.scalar(select(func.count()).select_from(Campaign).where(Campaign.telegram_user_id == owner_id, Campaign.status.in_(["running", "scheduled"])))
    return {
        "status": "running",
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN),
        "campaigns": campaigns,
        "active_campaigns": active,
        "mode": "campaign studio",
        "time": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    # One process owns the local queue. Restart explicitly after code/env edits.
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
