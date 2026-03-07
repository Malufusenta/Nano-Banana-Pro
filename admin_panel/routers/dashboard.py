"""
Дашборд — главная страница с живыми метриками
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select, func, or_

from app.database import async_session
from app.models import User, Purchase, BananaTransaction
from app.services.analytics_service import get_analytics_report
from admin_panel.routers.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def get_period_dates(period: str):
    now = datetime.utcnow()
    if period == "today":
        date_from = now.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = now
    elif period == "yesterday":
        yesterday = now - timedelta(days=1)
        date_from = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = yesterday.replace(hour=23, minute=59, second=59)
    elif period == "week":
        date_from = now - timedelta(days=7)
        date_to = now
    elif period == "month":
        date_from = now - timedelta(days=30)
        date_to = now
    else:  # alltime
        date_from = datetime(2020, 1, 1)
        date_to = now
    return date_from, date_to


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})


@router.get("/api/dashboard/stats")
async def dashboard_stats(request: Request, period: str = "today"):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    date_from, date_to = get_period_dates(period)

    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)

        # Всего юзеров в системе
        total_users = await session.scalar(select(func.count(User.id))) or 0

        # Генераций за период
        total_gens = await session.scalar(
            select(func.count(BananaTransaction.id)).where(
                BananaTransaction.transaction_type == "spent",
                BananaTransaction.created_at >= date_from,
                BananaTransaction.created_at <= date_to
            )
        ) or 0

    rev = data["revenue"]
    users_data = data["users"]

    return JSONResponse({
        "revenue": rev["rub_revenue"],
        "stars_revenue": rev["stars_revenue"],
        "stars_count": rev["stars_count"],
        "transactions": rev["transactions"],
        "avg_check": rev["avg_check"],
        "new_users": users_data["new"],
        "active_users": users_data["active"],
        "total_buyers": users_data["total_buyers"],
        "conversion_rate": users_data["conversion_rate"],
        "total_users": total_users,
        "total_gens": total_gens,
        "retention": rev.get("retention", 0),
        "ltv": rev.get("ltv", 0),
    })


@router.get("/api/dashboard/chart")
async def dashboard_chart(request: Request, days: int = 30):
    """График выручки за последние N дней"""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    async with async_session() as session:
        result = []
        now = datetime.utcnow()

        for i in range(days - 1, -1, -1):
            day = now - timedelta(days=i)
            date_from = day.replace(hour=0, minute=0, second=0, microsecond=0)
            date_to = day.replace(hour=23, minute=59, second=59)

            revenue = await session.scalar(
                select(func.sum(Purchase.price)).where(
                    Purchase.status == "succeeded",
                    Purchase.completed_at >= date_from,
                    Purchase.completed_at <= date_to,
                    or_(Purchase.tariff_name != "Telegram Stars", Purchase.tariff_name.is_(None))
                )
            ) or 0

            new_users = await session.scalar(
                select(func.count(User.id)).where(
                    User.created_at >= date_from,
                    User.created_at <= date_to
                )
            ) or 0

            result.append({
                "date": day.strftime("%d.%m"),
                "revenue": float(revenue),
                "new_users": new_users
            })

    return JSONResponse(result)
