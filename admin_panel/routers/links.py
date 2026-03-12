"""
Ссылки / Посты — аналитика по post_configs
"""
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from sqlalchemy import text
from pathlib import Path

from app.database import async_session
from admin_panel.routers.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return any(x in ua for x in ["mobile", "android", "iphone", "ipad"])


@router.get("/links", response_class=HTMLResponse)
async def links_page(request: Request, user=Depends(require_auth)):
    template = "mobile/links.html" if is_mobile(request) else "desktop/links.html"
    return templates.TemplateResponse(template, {"request": request, "user": user})


@router.get("/api/links/posts")
async def get_posts(user=Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT 
                pc.id,
                pc.config_id,
                pc.prompt,
                pc.model_type,
                pc.aspect_ratio,
                pc.created_at,
                pc.clicks_count,
                COUNT(DISTINCT u.telegram_id) as new_users,
                COUNT(DISTINCT pu.user_id) as buyers,
                COALESCE(SUM(pu.price), 0) as revenue,
                COUNT(pu.id) as purchases,
                COUNT(DISTINCT bt.user_id) as gen_users,
                ABS(COALESCE(SUM(bt.amount), 0)) as generations
            FROM post_configs pc
            LEFT JOIN users u ON u.source = 'post_' || pc.config_id
            LEFT JOIN purchases pu ON pu.user_source = 'post_' || pc.config_id AND pu.status = 'succeeded'
            LEFT JOIN banana_transactions bt ON bt.post_id = pc.config_id AND bt.transaction_type = 'spent'
            GROUP BY pc.id, pc.config_id, pc.prompt, pc.model_type, pc.aspect_ratio, pc.created_at, pc.clicks_count
            ORDER BY pc.created_at DESC
        """))
        rows = result.all()

    posts = []
    for r in rows:
        clicks = r.clicks_count or 0
        users = r.new_users or 0
        buyers = r.buyers or 0
        revenue = r.revenue or 0
        purchases = r.purchases or 0
        cr_clicks = round(users / clicks * 100, 1) if clicks else 0
        cr_buyers = round(buyers / users * 100, 1) if users else 0
        avg_check = round(revenue / purchases, 0) if purchases else 0
        prompt_text = (r.prompt or "").strip().replace("\n", " ")
        prompt_preview = prompt_text[:120] + "..." if len(prompt_text) > 120 else prompt_text

        posts.append({
            "id": r.id,
            "config_id": r.config_id,
            "prompt_preview": prompt_preview,
            "prompt_full": (r.prompt or "").strip(),
            "model_type": r.model_type or "—",
            "aspect_ratio": r.aspect_ratio or "—",
            "created_at": r.created_at.strftime("%d.%m.%Y") if r.created_at else "—",
            "clicks": clicks,
            "users": users,
            "buyers": buyers,
            "revenue": revenue,
            "purchases": purchases,
            "cr_clicks": cr_clicks,
            "cr_buyers": cr_buyers,
            "avg_check": avg_check,
            "gen_users": r.gen_users or 0,
            "generations": r.generations or 0,
        })

    totals = {
        "posts": len(posts),
        "clicks": sum(p["clicks"] for p in posts),
        "users": sum(p["users"] for p in posts),
        "buyers": sum(p["buyers"] for p in posts),
        "revenue": sum(p["revenue"] for p in posts),
    }

    return JSONResponse({"posts": posts, "totals": totals})
