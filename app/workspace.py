"""Backend-owned workspace defaults, editable message templates and UI metadata."""
import os
from string import Formatter

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.database import async_session
from app.models import WorkspaceSettings, Campaign, Conversion
from app.studio import owner
from app.config import DIRECT_SEND_MAX_RECIPIENTS

router = APIRouter(prefix="/api/studio")
STAGES = ("new", "warm", "hot", "converted", "closed")
CONVERSION_STATUSES = ("confirmed", "pending", "refunded", "cancelled")
CONVERSION_KINDS = ("purchase", "signup", "booking", "lead", "other")
PRIMARY_METRICS = ("signups", "conversions", "clicks", "replies")
CAMPAIGN_STATUSES = ("draft", "scheduled", "running", "paused", "stopped", "completed")
TEMPLATE_FIELDS = {"product", "benefit", "audience", "proof", "offer", "cta", "info_bank", "language", "tone"}


class DraftTemplate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    attention: str = Field(min_length=1, max_length=1000)
    interest: str = Field(min_length=1, max_length=1000)
    desire: str = Field(min_length=1, max_length=1000)
    action: str = Field(min_length=1, max_length=1000)

    @field_validator("attention", "interest", "desire", "action")
    @classmethod
    def valid_template(cls, value):
        if not value.strip():
            raise ValueError("Template sections cannot be blank")
        try:
            for _, name, spec, conversion in Formatter().parse(value):
                if name is not None and (name not in TEMPLATE_FIELDS or spec or conversion):
                    raise ValueError()
        except ValueError:
            raise ValueError("Use only supported placeholders with single braces and no format expressions") from None
        return value


class WorkspaceInput(BaseModel):
    default_category: str = Field(default="general", min_length=1, max_length=80)
    default_currency: str = Field(default_factory=lambda: os.getenv("DEFAULT_CURRENCY", "INR"), pattern="^[A-Z]{3}$")
    default_language: str = Field(default_factory=lambda: os.getenv("DEFAULT_LANGUAGE", "English"), min_length=1, max_length=80)
    default_tone: str = Field(default_factory=lambda: os.getenv("DEFAULT_TONE", "friendly"), min_length=1, max_length=80)
    default_source: str = Field(default="telegram", min_length=1, max_length=80)
    opt_out_alert_percent: float = Field(default=2, gt=0, le=100, allow_inf_nan=False)
    comparison_min_delivered: int = Field(default=100, ge=1, le=100000000)
    report_days: int = Field(default=30, ge=1, le=366)
    report_refresh_seconds: int = Field(default=30, ge=10, le=3600)
    draft_templates: list[DraftTemplate] = Field(default_factory=lambda: [
        DraftTemplate(label="A · Benefit first", attention="Looking for: {benefit}?", interest="{product} — {benefit}.", desire="{benefit}", action="{cta}"),
        DraftTemplate(label="B · Audience first", attention="{product}: {benefit}.", interest="For {audience}: {product} — {benefit}.", desire="{benefit}", action="{cta}"),
    ], min_length=1, max_length=5)

    @field_validator("default_category", "default_language", "default_tone", "default_source")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("Enter a non-empty value")
        return value.strip()


async def workspace_values(owner_id):
    async with async_session() as session:
        row = await session.get(WorkspaceSettings, owner_id)
        return WorkspaceInput.model_validate(row.values if row else {}).model_dump()


@router.get("/workspace")
async def workspace(owner_id=Depends(owner)):
    values = await workspace_values(owner_id)
    async with async_session() as session:
        currencies = set((await session.scalars(select(Campaign.currency).where(Campaign.telegram_user_id == owner_id))).all())
        currencies.update((await session.scalars(select(Conversion.currency).join(Campaign).where(Campaign.telegram_user_id == owner_id))).all())
    return {"settings": values, "currencies": sorted(currencies | {values["default_currency"]}),
            "options": {"stages": STAGES, "conversion_statuses": CONVERSION_STATUSES, "conversion_kinds": CONVERSION_KINDS,
                        "primary_metrics": PRIMARY_METRICS, "campaign_statuses": CAMPAIGN_STATUSES},
            "template_fields": sorted(TEMPLATE_FIELDS), "limits": {"direct_recipients": DIRECT_SEND_MAX_RECIPIENTS, "message_length": 4096}}


@router.put("/workspace")
async def save_workspace(data: WorkspaceInput, owner_id=Depends(owner)):
    async with async_session() as session:
        row = await session.get(WorkspaceSettings, owner_id)
        if row:
            row.values = data.model_dump()
        else:
            session.add(WorkspaceSettings(owner_id=owner_id, values=data.model_dump()))
        await session.commit()
    return await workspace(owner_id=owner_id)
