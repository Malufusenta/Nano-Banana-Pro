from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from datetime import datetime, timedelta, timezone
from app.database import async_session
from app.services.analytics_service import get_analytics_report
from .auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="admin_panel/templates")


def get_today_start_msk():
    now_utc = datetime.now(timezone.utc)
    msk_tz = timezone(timedelta(hours=3))
    now_msk = now_utc.astimezone(msk_tz)
    start_of_day_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day_msk.replace(tzinfo=None)

def get_date_range(period: str):
    now = datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)
    msk_tz = timezone(timedelta(hours=3))
    today_start = get_today_start_msk()
    today_end = now

    if period == "today":
        return today_start, today_end
    elif period == "yesterday":
        yesterday_start = today_start - timedelta(days=1)
        yesterday_end = today_start - timedelta(seconds=1)
        return yesterday_start, yesterday_end
    elif period == "week":
        return today_start - timedelta(days=7), today_end
    elif period == "month":
        return today_start - timedelta(days=30), today_end
    else:
        return datetime(2020, 1, 1), today_end

@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, user=Depends(require_auth)):
    return templates.TemplateResponse("analytics.html", {"request": request, "user": user})


@router.get("/api/analytics/sources")
async def get_sources(period: str = Query(default="month"), user=Depends(require_auth)):
    date_from, date_to = get_date_range(period)
    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)

    source_stats = data.get("source_stats", {})
    revenue_by_source = data.get("revenue_by_source", {})

    all_sources = set(list(source_stats.keys()) + list(revenue_by_source.keys()))
    result = []
    for source in all_sources:
        stats = source_stats.get(source, {})
        rev = revenue_by_source.get(source, {})
        buyers = stats.get("fresh_buyers", 0) + stats.get("delayed_buyers", 0)
        revenue = rev.get("revenue", 0)
        avg_check = round(revenue / rev.get("count", 1), 2) if rev.get("count", 0) > 0 else 0
        result.append({
            "source": source,
            "users": stats.get("total_users", 0),
            "buyers": buyers,
            "revenue": revenue,
            "avg_check": avg_check,
            "conversion": round(stats.get("conversion_percent", 0), 2),
            "new_revenue": rev.get("new_revenue", 0),
            "old_revenue": rev.get("old_revenue", 0),
        })
    result.sort(key=lambda x: x["revenue"], reverse=True)
    return result


@router.get("/api/analytics/funnel")
async def get_funnel(period: str = Query(default="month"), user=Depends(require_auth)):
    date_from, date_to = get_date_range(period)
    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)

    rev = data.get("revenue", {})
    users_data = data.get("users", {})
    return {
        "new_users": users_data.get("new", 0),
        "active_users": users_data.get("active", 0),
        "total_buyers": users_data.get("total_buyers", 0),
        "new_buyers": users_data.get("newbie_buyers", 0),
        "veteran_buyers": users_data.get("veteran_buyers", 0),
        "conversion_rate": round(users_data.get("conversion_rate", 0), 2),
        "avg_check": round(rev.get("avg_check", 0), 2),
        "revenue": rev.get("total", 0),
        "retention": round(rev.get("retention", 0), 2),
        "ltv": round(rev.get("ltv", 0), 2),
    }
