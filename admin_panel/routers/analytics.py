from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from datetime import datetime, timedelta, timezone
from app.database import async_session
from app.services.analytics_service import get_analytics_report, get_campaign_stats
from app.services.yandex_direct import get_direct_spending
from app.models import CampaignMapping
from sqlalchemy import select
from .auth import require_auth
from app import config
import json

router = APIRouter()
templates = Jinja2Templates(directory="admin_panel/templates")


def is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return any(x in ua for x in ["mobile", "android", "iphone", "ipad"])


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
    template = "mobile/analytics.html" if is_mobile(request) else "desktop/analytics.html"
    return templates.TemplateResponse(template, {"request": request, "user": user})


def _parse_dates(period: str, date_from: str = None, date_to: str = None):
    if date_from and date_to:
        df = datetime.strptime(date_from, "%Y-%m-%d")
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        return df, dt
    return get_date_range(period)


@router.get("/api/analytics/sources")
async def get_sources(
    period: str = Query(default="month"),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    user=Depends(require_auth)
):
    date_from, date_to = _parse_dates(period, date_from, date_to)
    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)

    source_stats = data.get("source_stats", {})
    revenue_by_source = data.get("revenue_by_source", {})

    all_sources = set(list(source_stats.keys()) + list(revenue_by_source.keys()))
    result = []
    for source in all_sources:
        stats = source_stats.get(source, {})
        rev = revenue_by_source.get(source, {})
        buyers = stats.get("fresh_buyers", 0)
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
async def get_funnel(
    period: str = Query(default="month"),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    user=Depends(require_auth)
):
    date_from, date_to = _parse_dates(period, date_from, date_to)
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


@router.get("/api/analytics/campaigns")
async def get_campaigns_stats(
    period: str = Query(default="month"),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    user=Depends(require_auth)
):
    date_from, date_to = _parse_dates(period, date_from, date_to)

    async with async_session() as session:
        campaign_stats = await get_campaign_stats(session, date_from, date_to)

    direct_data = {'total': 0, 'campaigns': {}, 'error': None}
    if config.YANDEX_DIRECT_TOKEN:
        direct_data = await get_direct_spending(
            config.YANDEX_DIRECT_TOKEN,
            date_from.date(),
            date_to.date()
        )

    rows = []
    for stat in campaign_stats:
        campaign_name = stat['campaign']
        spend = direct_data['campaigns'].get(campaign_name, 0)

        fast_b = stat['fast_buyers']
        new_revenue = stat['new_revenue']
        delayed_revenue = stat.get('delayed_revenue', 0)
        old_revenue = stat['old_revenue']
        total_revenue_full = new_revenue + delayed_revenue + old_revenue

        cac = round(spend / fast_b, 2) if fast_b > 0 else 0
        roas_new = round(new_revenue / spend * 100, 1) if spend > 0 else 0
        roas_total = round(total_revenue_full / spend * 100, 1) if spend > 0 else 0

        rows.append({
            'campaign': campaign_name,
            'spend': spend,
            'starts': stat['starts'],
            'fast_buyers': stat['fast_buyers'],
            'slow_buyers': stat['slow_buyers'],
            'cac': cac,
            'new_revenue': new_revenue,
            'delayed_revenue': delayed_revenue,
            'roas_new': roas_new,
            'old_revenue': old_revenue,
            'old_buyers': stat.get('old_buyers', 0),
            'roas_total': roas_total,
        })

    total_spend = sum(r['spend'] for r in rows)
    total_starts = sum(r['starts'] for r in rows)
    total_fast = sum(r['fast_buyers'] for r in rows)
    total_slow = sum(r['slow_buyers'] for r in rows)
    total_buyers_all = total_fast
    total_new_rev = sum(r['new_revenue'] for r in rows)
    total_delayed_rev = sum(r.get('delayed_revenue', 0) for r in rows)
    total_old_rev = sum(r['old_revenue'] for r in rows)
    total_rev_all = total_new_rev + total_delayed_rev + total_old_rev

    totals = {
        'campaign': 'ИТОГО',
        'spend': round(total_spend, 2),
        'starts': total_starts,
        'fast_buyers': total_fast,
        'slow_buyers': total_slow,
        'cac': round(total_spend / total_buyers_all, 2) if total_buyers_all > 0 else 0,
        'new_revenue': total_new_rev,
        'delayed_revenue': total_delayed_rev,
        'roas_new': round(total_new_rev / total_spend * 100, 1) if total_spend > 0 else 0,
        'old_revenue': total_old_rev,
        'old_buyers': sum(r['old_buyers'] for r in rows),
        'roas_total': round(total_rev_all / total_spend * 100, 1) if total_spend > 0 else 0,
    }

    return {'rows': rows, 'totals': totals, 'direct_error': direct_data.get('error')}


@router.get("/api/analytics/campaign-mappings")
async def get_mappings(user=Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(select(CampaignMapping).order_by(CampaignMapping.id))
        mappings = result.scalars().all()
    return [
        {
            'id': m.id,
            'yandex_campaign_name': m.yandex_campaign_name,
            'utm_sources': json.loads(m.utm_sources)
        }
        for m in mappings
    ]


@router.post("/api/analytics/campaign-mappings")
async def save_mapping(data: dict, user=Depends(require_auth)):
    async with async_session() as session:
        mapping_id = data.get('id')
        if mapping_id:
            result = await session.execute(
                select(CampaignMapping).where(CampaignMapping.id == mapping_id)
            )
            mapping = result.scalar_one_or_none()
        else:
            mapping = None

        if mapping:
            mapping.yandex_campaign_name = data['yandex_campaign_name']
            mapping.utm_sources = json.dumps(data['utm_sources'], ensure_ascii=False)
        else:
            mapping = CampaignMapping(
                yandex_campaign_name=data['yandex_campaign_name'],
                utm_sources=json.dumps(data['utm_sources'], ensure_ascii=False)
            )
            session.add(mapping)

        await session.commit()
    return {'ok': True}


@router.delete("/api/analytics/campaign-mappings/{mapping_id}")
async def delete_mapping(mapping_id: int, user=Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(
            select(CampaignMapping).where(CampaignMapping.id == mapping_id)
        )
        mapping = result.scalar_one_or_none()
        if mapping:
            await session.delete(mapping)
            await session.commit()
    return {'ok': True}
