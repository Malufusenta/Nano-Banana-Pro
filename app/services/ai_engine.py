import os
import asyncio
import io
import base64
import requests
import json
import time
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types
from aiogram.types import BufferedInputFile
from aiogram import Bot
from pathlib import Path
from app import config

# 1. Загрузка ключей
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

KIE_KEY = os.getenv("KIE_API_KEY")

# Настройки Kie.ai
KIE_URL = "https://api.kie.ai/api/v1/jobs"
KIE_MODEL_EDIT = "google/nano-banana-edit"
KIE_MODEL_GEN = "google/nano-banana"
KIE_MODEL_PRO = "nano-banana-pro"

# ==============================================================================
# 1. ДВИЖОК GOOGLE (ЗАГЛУШКА)
# ==============================================================================
async def _run_google_async(bot: Bot, prompt: str, image_urls=None, aspect_ratio: str = "1:1", history: list = None):
    print("⚠️ Запрос в Google пропущен (режим Kie Only).")
    return None

# ==============================================================================
# 2. ДВИЖОК KIE.AI (ОСНОВНОЙ)
# ==============================================================================
# 👇 ДОБАВИЛ АРГУМЕНТ resolution
def _run_kie(prompt: str, image_urls=None, aspect_ratio: str = "1:1", use_pro: bool = False, history: list = None, resolution: str = "1K"):
    if not config.KIE_API_KEY:
        print("❌ KIE ключ не настроен")
        return None

    # --- ФОРМИРОВАНИЕ ПРОМПТА С ПАМЯТЬЮ ---
    final_prompt = prompt
    if history:
        context_str = ""
        recent_history = history[-2:] 
        for msg in recent_history:
            role = "User" if msg.role == "user" else "AI"
            text_content = msg.content if msg.content != "Image generated" else "[Image]"
            context_str += f"{role}: {text_content}. "
        final_prompt = f"Context: {context_str} \nCURRENT TASK: {prompt}"

    # --- ВЫБОР МОДЕЛИ ---
    if use_pro:
        model = config.KIE_MODEL_PRO
        mode_name = "PRO"
    elif image_urls and len(image_urls) > 0:
        model = config.KIE_MODEL_EDIT
        mode_name = "EDIT (Multi-Image)"
    else:
        model = config.KIE_MODEL_GEN
        mode_name = "GEN"

    print(f"💎 [KIE CORE] Mode: {mode_name} | Res: {resolution} | Imgs: {len(image_urls) if image_urls else 0}")

    # --- СБОРКА PARAMETERS ---
    input_data = {
        "prompt": final_prompt,
        "output_format": "png"
    }

    if image_urls and not use_pro: 
        if isinstance(image_urls, str): image_urls = [image_urls]
        input_data["image_urls"] = image_urls
        input_data["strength"] = 0.85 
        input_data["guidance_scale"] = 7.5

    # Логика PRO
    if "pro" in model.lower():
        input_data["aspect_ratio"] = aspect_ratio
        # 👇 ИСПОЛЬЗУЕМ ПЕРЕДАННОЕ РАЗРЕШЕНИЕ (или дефолт 1K)
        input_data["resolution"] = resolution 
        
        if use_pro and image_urls:
             if isinstance(image_urls, str): image_urls = [image_urls]
             input_data["image_input"] = image_urls
    else:
        # 👇 ИСПРАВЛЕНО: Убрали принудительный "auto"
        input_data["image_size"] = aspect_ratio

    headers = {"Authorization": f"Bearer {config.KIE_API_KEY}", "Content-Type": "application/json"}
    
    try:
        resp = requests.post(f"{config.KIE_URL}/createTask", headers=headers, json={"model": model, "input": input_data})
        if resp.status_code != 200:
            print(f"❌ Kie Error: {resp.text}")
            return None
        
        resp_json = resp.json()
        if resp_json.get("code") != 200:
             print(f"❌ Kie Logic Error: {resp_json.get('msg')}")
             return None

        task_id = resp_json["data"]["taskId"]
        
        # Ждем до 10 минут
        for _ in range(300): 
            r = requests.get(f"{config.KIE_URL}/recordInfo", headers=headers, params={"taskId": task_id})
            data = r.json()["data"]
            
            if data and data.get("state") == "success":
                result_obj = json.loads(data["resultJson"])
                url = result_obj.get("resultUrls", [])[0]
                print(f"✨ Kie: Успех! (Task {task_id})")
                
                img_resp = requests.get(url)
                # Возвращаем (файл, ссылка)
                return BufferedInputFile(img_resp.content, filename=f"kie_{model}.png"), url
            
            elif data and data.get("state") == "fail":
                print(f"❌ Kie Failed: {data.get('failMsg')}")
                return None
            time.sleep(2)
            
    except Exception as e:
        print(f"❌ Kie Exception: {e}")
    return None

# ==============================================================================
# 3. ГЛАВНЫЙ РОУТЕР
# ==============================================================================
# 👇 ДОБАВИЛ resolution В АРГУМЕНТЫ
async def generate_image(bot: Bot, prompt: str, image_urls: list = None, is_premium: bool = False, aspect_ratio: str = "1:1", use_pro_model: bool = False, history: list = None, resolution: str = "1K"):
    return await asyncio.to_thread(_run_kie, prompt, image_urls, aspect_ratio, use_pro_model, history, resolution)