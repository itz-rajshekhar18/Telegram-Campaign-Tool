"""Recorded conversions and analytics, with explicit provenance for each metric."""
import csv
import io
from datetime import date, timedelta
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from app.database import async_session
from app.models import Campaign, CampaignVariant, Subscriber, Conversion, MetricSnapshot, Delivery, Broadcast, InboxMessage
from app.studio import owner, campaign_for

router = APIRouter(prefix="/api/studio")


class ConversionInput(BaseModel):
    campaign_id: int = Field(gt=0)
    subscriber_id: int | None = Field(default=None, gt=0)
    variant_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["purchase", "signup", "booking", "lead", "other"] = "purchase"
    status: Literal["pending", "confirmed", "refunded", "cancelled"] = "confirmed"
    amount: float = Field(default=0, ge=0, le=1000000000, allow_inf_nan=False)
    currency: str = Field(default="INR", pattern="^[A-Z]{3}$")
    source: str = Field(default="telegram", min_length=1, max_length=80)
    reference: str | None = Field(default=None, max_length=120)
    occurred_on: date
    notes: str = Field(default="", max_length=4000)

    @field_validator("name", "source")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip()

    @field_validator("reference")
    @classmethod
    def reference_or_none(cls, value):
        return value.strip() or None if value else None

    @field_validator("amount")
    @classmethod
    def rounded_amount(cls, value):
        return round(value, 2)


def bounds(start: date | None, end: date | None):
    end = end or date.today()
    start = start or end - timedelta(days=29)
    if end < start:
        raise HTTPException(422, "End date must be on or after the start date.")
    if (end - start).days > 365:
        raise HTTPException(422, "Choose a range of at most 366 days.")
    return start, end


async def configured_bounds(start, end, owner_id):
    if start is None:
        from app.workspace import workspace_values
        end = end or date.today()
        start = end - timedelta(days=(await workspace_values(owner_id))["report_days"] - 1)
    return bounds(start, end)


async def validate_links(session, data, owner_id):
    await campaign_for(session, data.campaign_id, owner_id)
    for cls, identifier in ((Subscriber, data.subscriber_id), (CampaignVariant, data.variant_id)):
        if identifier is not None:
            row = await session.get(cls, identifier)
            if not row or row.campaign_id != data.campaign_id:
                raise HTTPException(422, "The contact and variant must belong to the selected campaign.")


def conversion_payload(row, campaign_name=""):
    return {**{key: getattr(row, key) for key in ("id", "campaign_id", "subscriber_id", "variant_id", "name", "kind", "status", "amount", "currency", "source", "reference", "occurred_on", "notes", "created_at")}, "campaign_name": campaign_name}


@router.post("/conversions")
async def create_conversion(data: ConversionInput, owner_id=Depends(owner)):
    from app.workspace import workspace_values
    defaults = await workspace_values(owner_id)
    if "currency" not in data.model_fields_set:
        data.currency = defaults["default_currency"]
    if "source" not in data.model_fields_set:
        data.source = defaults["default_source"]
    async with async_session() as session:
        await validate_links(session, data, owner_id)
        values = data.model_dump()
        values["occurred_on"] = data.occurred_on.isoformat()
        row = Conversion(**values)
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, "This reference is already recorded for this campaign.") from None
        return conversion_payload(row)


@router.put("/conversions/{conversion_id}")
async def update_conversion(conversion_id: int, data: ConversionInput, owner_id=Depends(owner)):
    async with async_session() as session:
        row = await session.get(Conversion, conversion_id)
        if not row:
            raise HTTPException(404, "Conversion not found")
        await campaign_for(session, row.campaign_id, owner_id)
        await validate_links(session, data, owner_id)
        for key, value in data.model_dump().items():
            setattr(row, key, value.isoformat() if key == "occurred_on" else value)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, "This reference is already recorded for this campaign.") from None
        return conversion_payload(row)


@router.get("/conversions/{conversion_id}")
async def get_conversion(conversion_id: int, owner_id=Depends(owner)):
    async with async_session() as session:
        row = await session.get(Conversion, conversion_id)
        if not row:
            raise HTTPException(404, "Conversion not found")
        campaign = await campaign_for(session, row.campaign_id, owner_id)
        return conversion_payload(row, campaign.name)


def conversion_query(owner_id, campaign_id, start, end, status, search, currency):
    query = select(Conversion, Campaign.name).join(Campaign).where(
        Campaign.telegram_user_id == owner_id,
        Conversion.occurred_on >= start.isoformat(), Conversion.occurred_on <= end.isoformat())
    if campaign_id:
        query = query.where(Conversion.campaign_id == campaign_id)
    if status:
        query = query.where(Conversion.status == status)
    if currency:
        query = query.where(Conversion.currency == currency)
    if search:
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(Conversion.name.ilike(pattern, escape="\\") | Conversion.reference.ilike(pattern, escape="\\"))
    return query.order_by(Conversion.occurred_on.desc(), Conversion.id.desc())


@router.get("/conversions")
async def conversions(campaign_id: int | None = None, start: date | None = None, end: date | None = None,
                      status: Literal["pending", "confirmed", "refunded", "cancelled"] | None = None,
                      search: str = Query(default="", max_length=160), currency: str | None = Query(default=None, pattern="^[A-Z]{3}$"),
                      offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100), owner_id=Depends(owner)):
    start, end = await configured_bounds(start, end, owner_id)
    async with async_session() as session:
        if campaign_id:
            await campaign_for(session, campaign_id, owner_id)
        query = conversion_query(owner_id, campaign_id, start, end, status, search, currency)
        total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        rows = (await session.execute(query.offset(offset).limit(limit))).all()
        groups = defaultdict(lambda: {"confirmed": 0, "pending": 0, "refunded": 0, "cancelled": 0, "revenue": 0, "refund_value": 0})
        # Summaries use the entire filtered result, not just the current page.
        summary_rows = (await session.execute(select(Conversion.currency, Conversion.status, func.count(), func.sum(Conversion.amount)).where(Conversion.id.in_(query.with_only_columns(Conversion.id).order_by(None))).group_by(Conversion.currency, Conversion.status))).all()
        for code, state, count, value in summary_rows:
            groups[code][state] = count
            if state == "confirmed": groups[code]["revenue"] = round(value or 0, 2)
            if state == "refunded": groups[code]["refund_value"] = round(value or 0, 2)
        return {"items": [conversion_payload(row, name) for row, name in rows], "total": total, "offset": offset, "limit": limit,
                "summary": dict(groups), "start": start, "end": end}


@router.get("/conversion-export")
async def conversion_export(campaign_id: int | None = None, start: date | None = None, end: date | None = None,
                            status: Literal["pending", "confirmed", "refunded", "cancelled"] | None = None,
                            search: str = Query(default="", max_length=160), currency: str | None = Query(default=None, pattern="^[A-Z]{3}$"), owner_id=Depends(owner)):
    start, end = await configured_bounds(start, end, owner_id)
    async with async_session() as session:
        if campaign_id:
            await campaign_for(session, campaign_id, owner_id)
        rows = (await session.execute(conversion_query(owner_id, campaign_id, start, end, status, search, currency))).all()
        out = io.StringIO()
        writer = csv.writer(out)
        keys = ["id", "campaign_id", "name", "kind", "status", "amount", "currency", "source", "reference", "occurred_on", "notes"]
        writer.writerow(["campaign"] + keys)
        for row, name in rows:
            values = [name] + [getattr(row, key) for key in keys]
            values = [str(v) if v is not None else "" for v in values]
            writer.writerow(["'" + v if v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
        return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="conversions.csv"'})


@router.get("/analytics")
async def analytics(campaign_id: int | None = None, start: date | None = None, end: date | None = None,
                    currency: str | None = Query(default=None, pattern="^[A-Z]{3}$"), owner_id=Depends(owner)):
    if currency is None:
        from app.workspace import workspace_values
        currency = (await workspace_values(owner_id))["default_currency"]
    start, end = await configured_bounds(start, end, owner_id)
    a, b = start.isoformat(), end.isoformat()
    async with async_session() as session:
        if campaign_id:
            await campaign_for(session, campaign_id, owner_id)
        campaigns = (await session.scalars(select(Campaign).where(Campaign.telegram_user_id == owner_id))).all()
        campaigns = [c for c in campaigns if not campaign_id or c.id == campaign_id]
        ids = [c.id for c in campaigns]
        currency_ids = [c.id for c in campaigns if c.currency == currency]
        snapshots = (await session.scalars(select(MetricSnapshot).where(MetricSnapshot.campaign_id.in_(ids), MetricSnapshot.period >= a, MetricSnapshot.period <= b))).all()
        ledger = (await session.scalars(select(Conversion).where(Conversion.campaign_id.in_(ids), Conversion.occurred_on >= a, Conversion.occurred_on <= b))).all()
        delivery_rows = (await session.execute(select(Delivery.status, func.date(func.coalesce(Delivery.sent_at, Broadcast.scheduled_at)), Broadcast.campaign_id).join(Broadcast).where(Broadcast.campaign_id.in_(ids), func.date(func.coalesce(Delivery.sent_at, Broadcast.scheduled_at)) >= a, func.date(func.coalesce(Delivery.sent_at, Broadcast.scheduled_at)) <= b))).all()
        replies = await session.scalar(select(func.count()).select_from(InboxMessage).where(InboxMessage.campaign_id.in_(ids), InboxMessage.direction == "in", func.date(InboxMessage.created_at) >= a, func.date(InboxMessage.created_at) <= b))
        stages = (await session.execute(select(Subscriber.stage, func.count()).where(Subscriber.campaign_id.in_(ids)).group_by(Subscriber.stage))).all()
        trend = { (start + timedelta(days=i)).isoformat(): {"day": (start + timedelta(days=i)).isoformat(), "sent": 0, "clicks": 0, "manual_conversions": 0, "recorded_conversions": 0, "revenue": 0} for i in range((end - start).days + 1)}
        performance = {c.id: {"id": c.id, "name": c.name, "currency": c.currency, "budget": c.budget, "sent": 0, "delivered": 0, "clicks": 0, "manual_conversions": 0, "recorded_conversions": 0, "spend": 0, "revenue": 0} for c in campaigns}
        totals = {key: sum(getattr(row, key) for row in snapshots) for key in ("delivered", "clicks", "conversions", "signups", "replies", "opt_outs", "complaints")}
        counts = defaultdict(int)
        for state, day, cid in delivery_rows:
            counts[state] += 1
            if state == "sent":
                trend[day]["sent"] += 1
                performance[cid]["sent"] += 1
        for row in snapshots:
            trend[row.period]["clicks"] += row.clicks
            trend[row.period]["manual_conversions"] += row.conversions
            p = performance[row.campaign_id]
            p["delivered"] += row.delivered; p["clicks"] += row.clicks; p["manual_conversions"] += row.conversions
            if row.campaign_id in currency_ids: p["spend"] += row.spend
        sources = defaultdict(lambda: {"count": 0, "revenue": 0})
        for row in ledger:
            if row.status == "confirmed":
                trend[row.occurred_on]["recorded_conversions"] += 1
                performance[row.campaign_id]["recorded_conversions"] += 1
                sources[row.source]["count"] += 1
                if row.currency == currency:
                    trend[row.occurred_on]["revenue"] += row.amount
                    performance[row.campaign_id]["revenue"] += row.amount
                    sources[row.source]["revenue"] += row.amount
        confirmed = [r for r in ledger if r.status == "confirmed"]
        monetary = [r for r in confirmed if r.currency == currency]
        revenue = round(sum(r.amount for r in monetary), 2)
        spend = round(sum(r.spend for r in snapshots if r.campaign_id in currency_ids), 2)
        available = sorted({c.currency for c in campaigns} | {r.currency for r in ledger} | {currency})
        return {"start": start, "end": end, "currency": currency, "currencies": available, "live": {"sent": counts["sent"], "failed": counts["failed"], "unknown": counts["unknown"], "replies": replies, "statuses": dict(counts)},
                "manual": totals, "ledger": {"confirmed": len(confirmed), "pending": sum(r.status == "pending" for r in ledger), "revenue": revenue, "refund_value": round(sum(r.amount for r in ledger if r.status == "refunded" and r.currency == currency), 2)},
                "finance": {"spend": spend, "budget": round(sum(c.budget for c in campaigns if c.currency == currency), 2), "roas": round(revenue / spend, 2) if spend else None},
                "rates": {"click_rate": round(totals["clicks"] / totals["delivered"] * 100, 2) if totals["delivered"] else None, "conversion_rate": round(totals["conversions"] / totals["delivered"] * 100, 2) if totals["delivered"] else None},
                "trend": list(trend.values()), "campaigns": list(performance.values()), "stages": dict(stages), "sources": [{"source": key, **value} for key, value in sources.items()]}
