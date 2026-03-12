"""
Финансы — детальная финансовая аналитика
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from sqlalchemy import select, func, or_, text
from pathlib import Path

from app.database import async_session
from app.models import User, Purchase
from admin_panel.routers.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return any(x in ua for x in ["mobile", "android", "iphone", "ipad"])


def get_today_start_msk():
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz)
    start = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.replace(tzinfo=None)


def get_period_dates(period: str):
    msk_tz = timezone(timedelta(hours=3))
    now = datetime.now(msk_tz).replace(tzinfo=None)
    today_start = get_today_start_msk()
    if period == "today":
        return today_start, now
    elif period == "yesterday":
        return today_start - timedelta(days=1), today_start - timedelta(seconds=1)
    elif period == "week":
        return today_start - timedelta(days=7), now
    elif period == "month":
        return today_start - timedelta(days=30), now
    elif period == "quarter":
        return today_start - timedelta(days=90), now
    else:
        return datetime(2020, 1, 1), now


@router.get("/finances", response_class=HTMLResponse)
async def finances_page(request: Request, user=Depends(require_auth)):
    template = "mobile/finances.html" if is_mobile(request) else "desktop/finances.html"
    return templates.TemplateResponse(template, {"request": request, "user": user})


@router.get("/api/finances/summary")
async def finances_summary(
    period: str = Query(default="month"),
    date_from: str = None,
    date_to: str = None,
    user=Depends(require_auth)
):
    if date_from and date_to:
        df = datetime.strptime(date_from, "%Y-%m-%d")
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    else:
        df, dt = get_period_dates(period)

    async with async_session() as session:
        # Рублёвая выручка
        rub = await session.execute(select(
            func.sum(Purchase.price).label("revenue"),
            func.count(Purchase.id).label("count")
        ).where(
            Purchase.status == "succeeded",
            Purchase.completed_at >= df,
            Purchase.completed_at <= dt,
            or_(Purchase.tariff_name != "Telegram Stars", Purchase.tariff_name.is_(None))
        ))
        rub_data = rub.first()

        # Stars выручка
        stars = await session.execute(select(
            func.sum(Purchase.price).label("revenue"),
            func.count(Purchase.id).label("count")
        ).where(
            Purchase.status == "succeeded",
            Purchase.completed_at >= df,
            Purchase.completed_at <= dt,
            Purchase.tariff_name == "Telegram Stars"
        ))
        stars_data = stars.first()

        # Новые vs повторные
        first_buyers = await session.scalar(
            select(func.count(func.distinct(Purchase.user_id))).where(
                Purchase.status == "succeeded",
                Purchase.completed_at >= df,
                Purchase.completed_at <= dt,
                or_(Purchase.tariff_name != "Telegram Stars", Purchase.tariff_name.is_(None)),
                Purchase.user_id.in_(
                    select(User.telegram_id).where(
                        User.first_purchase_at >= df,
                        User.first_purchase_at <= dt
                    )
                )
            )
        ) or 0

        # По тарифам
        tariffs_result = await session.execute(select(
            Purchase.tariff_name,
            func.count(Purchase.id).label("count"),
            func.sum(Purchase.price).label("revenue")
        ).where(
            Purchase.status == "succeeded",
            Purchase.completed_at >= df,
            Purchase.completed_at <= dt,
        ).group_by(Purchase.tariff_name).order_by(func.sum(Purchase.price).desc()))
        tariffs = tariffs_result.all()

        # Динамика по дням
        daily_result = await session.execute(text("""
            SELECT DATE(completed_at) as day,
                   COUNT(*) as count,
                   SUM(price) as revenue
            FROM purchases
            WHERE status = 'succeeded'
            AND completed_at >= :df
            AND completed_at <= :dt
            AND (tariff_name != 'Telegram Stars' OR tariff_name IS NULL)
            GROUP BY day
            ORDER BY day
        """), {"df": df, "dt": dt})
        daily = daily_result.all()

        # Последние транзакции
        recent_result = await session.execute(select(
            Purchase.id,
            Purchase.user_id,
            Purchase.tariff_name,
            Purchase.price,
            Purchase.completed_at,
        ).where(
            Purchase.status == "succeeded",
            Purchase.completed_at >= df,
            Purchase.completed_at <= dt,
        ).order_by(Purchase.completed_at.desc()).limit(50))
        recent = recent_result.all()

    rub_revenue = rub_data.revenue or 0
    rub_count = rub_data.count or 0
    stars_revenue = stars_data.revenue or 0
    stars_count = stars_data.count or 0
    total_count = rub_count + stars_count
    repeat_buyers = total_count - first_buyers

    return JSONResponse({
        "rub_revenue": rub_revenue,
        "stars_revenue": stars_revenue,
        "stars_count": stars_count,
        "total_count": total_count,
        "rub_count": rub_count,
        "first_buyers": first_buyers,
        "repeat_buyers": repeat_buyers,
        "avg_check": round(rub_revenue / rub_count, 0) if rub_count else 0,
        "tariffs": [
            {"name": t.tariff_name or "—", "count": t.count, "revenue": t.revenue or 0}
            for t in tariffs
        ],
        "daily": [
            {"day": str(d.day), "count": d.count, "revenue": d.revenue or 0}
            for d in daily
        ],
        "recent": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "tariff": r.tariff_name or "—",
                "price": r.price,
                "time": r.completed_at.strftime("%d.%m %H:%M") if r.completed_at else "—"
            }
            for r in recent
        ]
    })
