"""
Настройки — тарифы, стоимость генераций, бэкап
"""
import subprocess
from datetime import datetime
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pathlib import Path

from admin_panel.routers.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

PACKAGES_PATH = "/home/Dianka/Nano-Banana-Pro/app/packages.py"
CONFIG_PATH = "/home/Dianka/Nano-Banana-Pro/app/config.py"
BACKUP_DIR = "/var/backups/postgres"


def read_packages():
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("packages", PACKAGES_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PACKAGES, mod.STARS_PACKAGES


def read_config():
    config = {}
    with open(CONFIG_PATH) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.split('#')[0].strip()
                if key.startswith('COST_') or key in ('BONUS_AMOUNT',):
                    try:
                        config[key] = int(val)
                    except:
                        pass
    return config


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user=Depends(require_auth)):
    return templates.TemplateResponse("settings.html", {"request": request, "user": user})


@router.get("/api/settings/data")
async def get_settings(user=Depends(require_auth)):
    packages, stars_packages = read_packages()
    config = read_config()
    return JSONResponse({
        "packages": packages,
        "stars_packages": stars_packages,
        "config": config,
    })


@router.post("/api/settings/packages")
async def save_packages(request: Request, user=Depends(require_auth)):
    body = await request.json()
    packages = body.get("packages", {})
    stars = body.get("stars_packages", {})

    lines = [
        "# Здесь мы храним настройки всех товаров.\n",
        "# Меняешь здесь — меняется везде: и в меню, и в обработке платежей.\n\n",
        "PACKAGES = {\n",
    ]
    for key, pkg in packages.items():
        lines.append(
            f'    "{key}": {{"name": "{pkg["name"]}", "gens": {int(pkg["gens"])}, '
            f'"price": {int(pkg["price"])}, "emoji": "{pkg.get("emoji","")}", '
            f'"suffix": "{pkg["suffix"]}"}},\n'
        )
    lines.append("}\n\n# Stars пакеты\nSTARS_PACKAGES = {\n")
    for key, pkg in stars.items():
        lines.append(
            f'    "{key}": {{"bananas": {int(pkg["bananas"])}, "stars": {int(pkg["stars"])}, '
            f'"emoji": "{pkg.get("emoji","🍌")}"}},\n'
        )
    lines.append("}\n")

    with open(PACKAGES_PATH, "w") as f:
        f.writelines(lines)

    return JSONResponse({"ok": True})


@router.post("/api/settings/config")
async def save_config(request: Request, user=Depends(require_auth)):
    body = await request.json()
    with open(CONFIG_PATH, "r") as f:
        content = f.read()

    for key, val in body.items():
        if not key.startswith("COST_") and key != "BONUS_AMOUNT":
            continue
        try:
            val = int(val)
        except:
            continue
        import re
        content = re.sub(
            rf"^({re.escape(key)}\s*=\s*)(\d+)",
            rf"\g<1>{val}",
            content,
            flags=re.MULTILINE
        )

    with open(CONFIG_PATH, "w") as f:
        f.write(content)

    return JSONResponse({"ok": True})


@router.post("/api/settings/backup")
async def run_backup(user=Depends(require_auth)):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{BACKUP_DIR}/manual_backup_{ts}.dump"
        result = subprocess.run([
            "pg_dump",
            "-U", "nanobana_user",
            "-h", "localhost",
            "-F", "c",
            "-f", filename,
            "nanobana_prod"
        ], capture_output=True, text=True,
           env={**__import__("os").environ, "PGPASSWORD": "dianka12345"})

        if result.returncode == 0:
            size = Path(filename).stat().st_size
            return JSONResponse({"ok": True, "filename": filename, "size": size})
        else:
            return JSONResponse({"ok": False, "error": result.stderr})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.get("/api/settings/backups")
async def list_backups(user=Depends(require_auth)):
    try:
        files = sorted(Path(BACKUP_DIR).glob("*.dump"), key=lambda x: x.stat().st_mtime, reverse=True)
        return JSONResponse({"backups": [
            {"name": f.name, "size": f.stat().st_size, "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M")}
            for f in files[:10]
        ]})
    except:
        return JSONResponse({"backups": []})


LOG_DIR = "/home/Dianka/Nano-Banana-Pro"

@router.get("/api/settings/logs/files")
async def list_log_files(user=Depends(require_auth)):
    import glob
    files = sorted(glob.glob(f"{LOG_DIR}/bot.log*"), reverse=True)
    return JSONResponse({"files": [Path(f).name for f in files]})

@router.get("/api/settings/logs")
async def get_logs(lines: int = 100, filename: str = "bot.log", user=Depends(require_auth)):
    try:
        filepath = f"{LOG_DIR}/{filename}"
        if not Path(filepath).exists():
            return JSONResponse({"logs": "Файл не найден"})
        result = subprocess.run(["tail", "-n", str(lines), filepath], capture_output=True, text=True)
        return JSONResponse({"logs": result.stdout})
    except Exception as e:
        return JSONResponse({"logs": f"Ошибка: {e}"})

@router.get("/api/settings/logs/download")
async def download_logs(filename: str = "bot.log", user=Depends(require_auth)):
    from fastapi.responses import FileResponse
    filepath = f"{LOG_DIR}/{filename}"
    if not Path(filepath).exists():
        return JSONResponse({"error": "Файл не найден"})
    return FileResponse(filepath, filename=filename, media_type="text/plain")
