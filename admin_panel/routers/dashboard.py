"""
Дашборд — главная страница с живыми метриками
"""
from datetime import datetime, timedelta
from decimal import Decimal
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.responses import JSONResponse as _BaseJSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select, func, or_, text

from app.database import async_session
from app.models import User, Purchase, BananaTransaction
from app.services.analytics_service import get_analytics_report
from app.services.currency import get_usd_rate
from admin_panel.routers.auth import get_current_user


class JSONResponse(_BaseJSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            cls=type('D', (json.JSONEncoder,), {
                'default': lambda self, o: float(o) if isinstance(o, Decimal) else super().default(o)
            })
        ).encode('utf-8')

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def get_today_start_msk():
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    msk_tz = timezone(timedelta(hours=3))
    now_msk = now_utc.astimezone(msk_tz)
    start_of_day_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day_msk.replace(tzinfo=None)

def get_period_dates(period: str):
    from datetime import timezone
    now = datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)
    today_start = get_today_start_msk()
    if period == "today":
        date_from = today_start
        date_to = now
    elif period == "yesterday":
        date_from = today_start - timedelta(days=1)
        date_to = today_start - timedelta(seconds=1)
    elif period == "week":
        date_from = today_start - timedelta(days=7)
        date_to = now
    elif period == "month":
        date_from = today_start - timedelta(days=30)
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
async def dashboard_stats(request: Request, period: str = "today", date_from: str = None, date_to: str = None):
    # Произвольный период
    if date_from and date_to:
        from datetime import datetime as dt
        df = dt.strptime(date_from, "%Y-%m-%d")
        dt_ = dt.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        user = get_current_user(request)
        if not user:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        usd_rate = await get_usd_rate()
        async with async_session() as session:
            data = await get_analytics_report(session, df, dt_)
            total_users = await session.scalar(select(func.count()).select_from(User))
            total_gens = await session.scalar(select(func.count()).select_from(User).where(User.total_generations_used > 0)) or 0
            bananas_given = await session.scalar(text("""
                SELECT COALESCE(SUM(amount), 0) FROM banana_transactions
                WHERE transaction_type = 'welcome'
                AND created_at >= :df AND created_at <= :dt
            """), {"df": df, "dt": dt_}) or 0
            bananas_spent = await session.scalar(text("""
                SELECT COALESCE(ABS(SUM(amount)), 0) FROM banana_transactions
                WHERE transaction_type = 'spent'
                AND created_at >= :df AND created_at <= :dt
            """), {"df": df, "dt": dt_}) or 0
        rev = data["revenue"]
        users_data = data["users"]

        return JSONResponse({
            "revenue": rev["rub_revenue"],
            "stars_revenue": rev["stars_revenue"],
            "stars_revenue_rub": rev.get("stars_revenue_rub", 0),
            "stars_net_rub": rev.get("stars_net_rub", 0),
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
            "bananas_given": int(bananas_given),
            "bananas_spent": int(bananas_spent),
            "purchases_by_tariff": data.get("purchases_by_tariff", {}),
            "top_sources": sorted([{"name": k, "revenue": v["revenue"], "count": v["count"]} for k, v in data.get("revenue_by_source", {}).items()], key=lambda x: x["revenue"], reverse=True)[:5],
            "usd_rate": usd_rate,
        })
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    date_from, date_to = get_period_dates(period)
    usd_rate = await get_usd_rate()

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

        bananas_given = await session.scalar(text("""
            SELECT COALESCE(SUM(amount), 0) FROM banana_transactions
            WHERE transaction_type = 'welcome'
            AND created_at >= :df AND created_at <= :dt
        """), {"df": date_from, "dt": date_to}) or 0

        bananas_spent = await session.scalar(text("""
            SELECT COALESCE(ABS(SUM(amount)), 0) FROM banana_transactions
            WHERE transaction_type = 'spent'
            AND created_at >= :df AND created_at <= :dt
        """), {"df": date_from, "dt": date_to}) or 0

    rev = data["revenue"]
    users_data = data["users"]

    return JSONResponse({
        "revenue": rev["rub_revenue"],
        "stars_revenue": rev["stars_revenue"],
        "stars_revenue_rub": rev.get("stars_revenue_rub", 0),
        "stars_net_rub": rev.get("stars_net_rub", 0),
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
        "bananas_given": int(bananas_given),
        "bananas_spent": int(bananas_spent),
        "top_sources": sorted([{"name": k, "revenue": v["revenue"], "count": v["count"]} for k, v in data.get("revenue_by_source", {}).items()], key=lambda x: x["revenue"], reverse=True)[:5],
        "purchases_by_tariff": data.get("purchases_by_tariff", {}),
        "funnel": data.get("funnel", {}),
        "direct": data.get("direct", {}),
        "fixed_expenses": data.get("fixed_expenses", {}),
        "kie": data.get("kie", {}),
        "blocked": data.get("users", {}).get("blocked", 0),
        "bananas_detail": {
            "ref": data.get("bananas", {}).get("earned_ref", 0),
            "welcome": data.get("bananas", {}).get("earned_welcome", 0),
            "channel": data.get("bananas", {}).get("earned_sub", 0),
            "purchased": data.get("bananas", {}).get("purchased", 0),
        },
        "usd_rate": usd_rate,
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
