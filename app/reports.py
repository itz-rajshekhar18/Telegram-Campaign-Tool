"""Reports reuse analytics and ledger calculations and persist their generated snapshot."""
import asyncio
import csv
import io
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from sqlalchemy import select

from app.database import async_session
from app.insights import analytics, configured_bounds, conversions, conversion_export
from app.models import SavedReport
from app.studio import owner

router = APIRouter(prefix="/api/studio")
report_lock = asyncio.Lock()


async def build_report(campaign_id, start, end, currency, owner_id):
    if currency is None:
        from app.workspace import workspace_values
        currency = (await workspace_values(owner_id))["default_currency"]
    start, end = await configured_bounds(start, end, owner_id)
    async with report_lock:
        data = await analytics(campaign_id=campaign_id, start=start, end=end, currency=currency, owner_id=owner_id)
        ledger = await conversions(campaign_id=campaign_id, start=start, end=end, status=None,
                                   search="", currency=None, offset=0, limit=10, owner_id=owner_id)
        payload = jsonable_encoder({"analytics": data, "conversions": ledger})
        async with async_session() as session:
            row = await session.scalar(select(SavedReport).where(
                SavedReport.owner_id == owner_id, SavedReport.scope_id == (campaign_id or 0),
                SavedReport.start == start.isoformat(), SavedReport.end == end.isoformat(), SavedReport.currency == currency))
            if row is None:
                row = SavedReport(owner_id=owner_id, scope_id=campaign_id or 0, start=start.isoformat(),
                                  end=end.isoformat(), currency=currency, payload=payload)
                session.add(row)
            elif row.payload != payload:
                row.payload = payload
                row.saved_at = datetime.utcnow()
            await session.commit()
            return {**payload, "id": row.id, "saved_at": row.saved_at.isoformat() + "Z", "auto_saved": True}


@router.get("/reports")
async def report(response: Response, campaign_id: int | None = Query(default=None, gt=0), start: date | None = None,
                 end: date | None = None, currency: str | None = Query(default=None, pattern="^[A-Z]{3}$"), owner_id=Depends(owner)):
    response.headers["Cache-Control"] = "no-store"
    return await build_report(campaign_id, start, end, currency, owner_id)


def csv_response(filename, keys, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(keys)
    for row in rows:
        values = [str(row.get(key, "")) if row.get(key) is not None else "" for key in keys]
        writer.writerow(["'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in values])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@router.get("/report-export")
async def export_report(kind: Literal["summary", "daily", "campaigns", "conversions"] = "summary",
                        campaign_id: int | None = Query(default=None, gt=0), start: date | None = None,
                        end: date | None = None, currency: str | None = Query(default=None, pattern="^[A-Z]{3}$"), owner_id=Depends(owner)):
    result = await build_report(campaign_id, start, end, currency, owner_id)
    d = result["analytics"]
    if kind == "conversions":
        # Full ledger export, not the ten-row preview stored with the report.
        return await conversion_export(campaign_id=campaign_id, start=date.fromisoformat(d["start"]),
                                       end=date.fromisoformat(d["end"]), status=None, search="", currency=None, owner_id=owner_id)
    metadata = {"report_id": result["id"], "start": d["start"], "end": d["end"], "selected_currency": d["currency"], "saved_at": result["saved_at"]}
    if kind == "summary":
        rows = [{**metadata, "source": group, "metric": key, "value": value,
                 "value_currency": d["currency"] if key in {"revenue", "refund_value", "spend", "budget"} else ""}
                for group in ("live", "manual", "ledger", "finance", "rates")
                for key, value in d[group].items() if not isinstance(value, dict)]
        for code, summary in result["conversions"]["summary"].items():
            rows.extend({**metadata, "source": "conversion_ledger_by_currency", "metric": key, "value": value,
                         "value_currency": code} for key, value in summary.items())
        keys = list(metadata) + ["source", "metric", "value", "value_currency"]
    else:
        source = d["trend"] if kind == "daily" else d["campaigns"]
        columns = (["day", "sent", "clicks", "manual_conversions", "recorded_conversions", "revenue"] if kind == "daily" else
                   ["id", "name", "currency", "budget", "sent", "delivered", "clicks", "manual_conversions", "recorded_conversions", "spend", "revenue"])
        rows = [{**metadata, **row} for row in source]
        keys = list(metadata) + columns
    return csv_response(f"report-{kind}-{d['start']}-{d['end']}.csv", keys, rows)
