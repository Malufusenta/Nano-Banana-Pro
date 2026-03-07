"""
Nano Banana Admin Panel — FastAPI
"""
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from admin_panel.routers import auth, dashboard, users, analytics, broadcast

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Nano Banana Admin", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(analytics.router)
app.include_router(broadcast.router)