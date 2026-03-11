"""
Nano Banana Admin Panel — FastAPI
"""
import os
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI
from fastapi.responses import JSONResponse as _JSONResponse
from decimal import Decimal
import json

class JSONResponse(_JSONResponse):
    def render(self, content):
        return json.dumps(content, cls=_DecimalEncoder).encode('utf-8')

class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from admin_panel.routers import auth, dashboard, users, analytics, broadcast, finances, links, settings

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Nano Banana Admin", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(analytics.router)
app.include_router(broadcast.router)
app.include_router(finances.router)
app.include_router(links.router)
app.include_router(settings.router)