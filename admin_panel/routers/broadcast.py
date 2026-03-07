from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from sqlalchemy import select, func, desc
from app.database import async_session
from app.models import Broadcast
from .auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="admin_panel/templates")


@router.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, user=Depends(require_auth)):
    return templates.TemplateResponse("broadcast.html", {"request": request, "user": user})


@router.get("/api/broadcasts")
async def get_broadcasts(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, le=100),
    user=Depends(require_auth),
):
    offset = (page - 1) * limit
    async with async_session() as session:
        total = await session.scalar(select(func.count()).select_from(Broadcast))
        result = await session.execute(
            select(Broadcast).order_by(desc(Broadcast.created_at)).offset(offset).limit(limit)
        )
        broadcasts = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
        "broadcasts": [
            {
                "id": b.id,
                "status": b.status or "—",
                "message_text": (b.message_text or "")[:200],
                "media_type": b.media_type or "text",
                "total_users": b.total_users or 0,
                "sent_count": b.sent_count or 0,
                "delivered_count": b.delivered_count or 0,
                "blocked_count": b.blocked_count or 0,
                "delivery_rate": round((b.delivered_count or 0) / (b.sent_count or 1) * 100, 1),
                "created_at": b.created_at.strftime("%d.%m.%Y %H:%M") if b.created_at else "—",
                "completed_at": b.completed_at.strftime("%d.%m.%Y %H:%M") if b.completed_at else "—",
            }
            for b in broadcasts
        ],
    }


@router.get("/api/broadcasts/{broadcast_id}")
async def get_broadcast_detail(broadcast_id: int, user=Depends(require_auth)):
    async with async_session() as session:
        b = await session.scalar(select(Broadcast).where(Broadcast.id == broadcast_id))
        if not b:
            return {"error": "Not found"}

    return {
        "id": b.id,
        "status": b.status or "—",
        "message_text": b.message_text or "",
        "media_type": b.media_type or "text",
        "total_users": b.total_users or 0,
        "sent_count": b.sent_count or 0,
        "delivered_count": b.delivered_count or 0,
        "blocked_count": b.blocked_count or 0,
        "delivery_rate": round((b.delivered_count or 0) / (b.sent_count or 1) * 100, 1),
        "created_at": b.created_at.strftime("%d.%m.%Y %H:%M") if b.created_at else "—",
        "started_at": b.started_at.strftime("%d.%m.%Y %H:%M") if b.started_at else "—",
        "completed_at": b.completed_at.strftime("%d.%m.%Y %H:%M") if b.completed_at else "—",
    }
