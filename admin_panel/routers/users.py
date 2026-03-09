from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from sqlalchemy import select, func, desc, or_
from app.database import async_session
from app.models import User, Purchase, BananaTransaction, GenerationTask
from .auth import require_auth, require_auth_api

router = APIRouter()
templates = Jinja2Templates(directory="admin_panel/templates")


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, user=Depends(require_auth)):
    return templates.TemplateResponse("users.html", {"request": request, "user": user})


@router.get("/api/users")
async def get_users(
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, le=100),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    user=Depends(require_auth),
):
    offset = (page - 1) * limit
    stmt = select(User)

    if q:
        try:
            tg_id = int(q)
            stmt = stmt.where(or_(User.telegram_id == tg_id, User.username.ilike(f"%{q}%"), User.full_name.ilike(f"%{q}%")))
        except ValueError:
            stmt = stmt.where(or_(User.username.ilike(f"%{q}%"), User.full_name.ilike(f"%{q}%")))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    sort_col = getattr(User, sort, User.created_at)
    stmt = stmt.order_by(desc(sort_col) if order == "desc" else sort_col)
    stmt = stmt.offset(offset).limit(limit)

    async with async_session() as session:
        total = await session.scalar(count_stmt)
        result = await session.execute(stmt)
        users = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
        "users": [{
            "telegram_id": u.telegram_id,
            "username": u.username or "",
            "full_name": u.full_name or "",
            "balance_paid": u.balance_paid or 0,
            "balance_free": u.balance_free or 0,
            "orders_count": u.orders_count or 0,
            "total_revenue": u.total_revenue or 0,
            "total_generations_used": u.total_generations_used or 0,
            "source": u.source or "—",
            "preferred_model": u.preferred_model or "—",
            "is_blocked": u.is_blocked or False,
            "created_at": u.created_at.strftime("%d.%m.%Y") if u.created_at else "—",
            "last_generation_at": u.last_generation_at.strftime("%d.%m.%Y %H:%M") if u.last_generation_at else "—",
            "first_purchase_at": u.first_purchase_at.strftime("%d.%m.%Y") if u.first_purchase_at else None,
        } for u in users],
    }


@router.get("/api/users/{telegram_id}")
async def get_user_detail(telegram_id: int, user=Depends(require_auth)):
    async with async_session() as session:
        u = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            raise HTTPException(status_code=404, detail="User not found")

        purchases = (await session.execute(
            select(Purchase).where(Purchase.user_id == telegram_id, Purchase.status == "succeeded")
            .order_by(desc(Purchase.completed_at)).limit(20)
        )).scalars().all()

        txns = (await session.execute(
            select(BananaTransaction).where(BananaTransaction.user_id == telegram_id)
            .order_by(desc(BananaTransaction.created_at)).limit(30)
        )).scalars().all()

        gens = (await session.execute(
            select(GenerationTask).where(GenerationTask.user_id == telegram_id)
            .order_by(desc(GenerationTask.created_at)).limit(20)
        )).scalars().all()

    return {
        "user": {
            "telegram_id": u.telegram_id,
            "username": u.username or "",
            "full_name": u.full_name or "",
            "balance_paid": u.balance_paid or 0,
            "balance_free": u.balance_free or 0,
            "generations_balance": u.generations_balance or 0,
            "total_generations_used": u.total_generations_used or 0,
            "orders_count": u.orders_count or 0,
            "total_revenue": u.total_revenue or 0,
            "source": u.source or "—",
            "preferred_model": u.preferred_model or "—",
            "is_blocked": u.is_blocked or False,
            "referrer_id": u.referrer_id,
            "created_at": u.created_at.strftime("%d.%m.%Y %H:%M") if u.created_at else "—",
            "last_generation_at": u.last_generation_at.strftime("%d.%m.%Y %H:%M") if u.last_generation_at else "—",
            "first_purchase_at": u.first_purchase_at.strftime("%d.%m.%Y") if u.first_purchase_at else "—",
        },
        "purchases": [{
            "id": p.id, "amount": p.amount, "price": p.price,
            "tariff_name": p.tariff_name or "—",
            "completed_at": p.completed_at.strftime("%d.%m.%Y %H:%M") if p.completed_at else "—",
        } for p in purchases],
        "transactions": [{
            "id": t.id, "amount": t.amount,
            "type": t.transaction_type or "—",
            "description": t.description or "—",
            "created_at": t.created_at.strftime("%d.%m.%Y %H:%M") if t.created_at else "—",
        } for t in txns],
        "generations": [{
            "id": g.id, "cost": g.cost, "status": g.status,
            "created_at": g.created_at.strftime("%d.%m.%Y %H:%M") if g.created_at else "—",
        } for g in gens],
    }


@router.post("/api/users/{telegram_id}/block")
async def toggle_block(telegram_id: int, request: Request, user=Depends(require_auth_api)):
    async with async_session() as session:
        u = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            raise HTTPException(status_code=404, detail="Not found")
        u.is_blocked = not u.is_blocked
        await session.commit()
        return {"ok": True, "is_blocked": u.is_blocked}


@router.post("/api/users/{telegram_id}/balance")
async def change_balance(telegram_id: int, request: Request, user=Depends(require_auth_api)):
    body = await request.json()
    amount = int(body.get("amount", 0))
    comment = body.get("comment", "Admin adjustment")
    async with async_session() as session:
        u = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            raise HTTPException(status_code=404, detail="Not found")
        u.balance_paid = max(0, (u.balance_paid or 0) + amount)
        u.generations_balance = u.balance_paid + (u.balance_free or 0)
        from app.models import BananaTransaction
        t = BananaTransaction(
            user_id=telegram_id,
            amount=amount,
            transaction_type="earned_manual" if amount > 0 else "spent",
            description=f"Admin: {comment}"
        )
        session.add(t)
        await session.commit()
        return {"ok": True, "new_balance": u.balance_paid}


@router.post("/api/users/{telegram_id}/message")
async def send_message(telegram_id: int, request: Request, user=Depends(require_auth_api)):
    import aiohttp, os
    body = await request.json()
    text = body.get("text", "")
    if not text:
        return {"ok": False, "error": "Пустое сообщение"}
    token = os.environ.get("BOT_TOKEN", "")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            "chat_id": telegram_id,
            "text": text,
            "parse_mode": "HTML"
        })
        data = await r.json()
    return {"ok": data.get("ok", False), "error": data.get("description", "")}
