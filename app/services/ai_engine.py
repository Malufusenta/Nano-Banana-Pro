import os
import asyncio
import re
import io
import base64
import aiohttp
import json
import ssl
import certifi
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
KIE_MODEL_2 = "nano-banana-2"

def sanitize_prompt(text: str) -> str:
    """Убирает переносы строк и мусор, чтобы API не ломался"""
    if not text: return ""
    # Меняем Enter на пробел
    text = text.replace("\n", " ").replace("\r", " ")
    # Убираем двойные пробелы
    text = re.sub(' +', ' ', text)
    # Обрезаем до 1500 символов (на всякий случай)
    return text[:1500].strip()
# ==============================================================================
# 2. ДВИЖОК KIE.AI (ОСНОВНОЙ)
# ==============================================================================
# 👇 ДОБАВИЛ АРГУМЕНТ resolution
async def _run_kie(prompt: str, image_urls=None, aspect_ratio: str = "1:1", use_pro: bool = False, use_nb2: bool = False, history: list = None, resolution: str = "1K"):
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
        # 👇 ДОБАВЬ ЭТУ СТРОКУ
        final_prompt = sanitize_prompt(final_prompt)

    # --- ВЫБОР МОДЕЛИ ---
    if use_pro:
        model = config.KIE_MODEL_PRO
        mode_name = "PRO"
    elif use_nb2:
        model = config.KIE_MODEL_2
        mode_name = "NB2"
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

    if use_nb2:
        input_data["aspect_ratio"] = aspect_ratio
        input_data["resolution"] = resolution
        if image_urls:
            if isinstance(image_urls, str): image_urls = [image_urls]
            input_data["image_input"] = image_urls

    elif image_urls and not use_pro: 
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
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(f"{config.KIE_URL}/createTask", headers=headers, json={"model": model, "input": input_data}) as resp:
                if resp.status != 200:
                    raise Exception(f"{resp.status} {await resp.text()}")
                
                resp_json = await resp.json()

            if resp_json.get("code") != 200:
                error_msg = resp_json.get('msg')
                raise Exception(f"API Error: {error_msg}")

            task_id = resp_json["data"]["taskId"]
        
            for _ in range(300): 
                try:
                    async with session.get(f"{config.KIE_URL}/recordInfo", headers=headers, params={"taskId": task_id}) as r:
                        data = (await r.json()).get("data")
                    
                    if not data:
                        await asyncio.sleep(5)
                        continue

                    state = data.get("state")

                    if state == "success":
                        result_json = data.get("resultJson")
                        if isinstance(result_json, str):
                            result_obj = json.loads(result_json)
                        else:
                            result_obj = result_json
                        
                        urls = result_obj.get("resultUrls", [])
                        if not urls:
                            raise Exception("No images found in AI response (Possible Soft Filter)")
                        
                        url = urls[0]
                        print(f"✨ Kie: Успех! (Task {task_id})")
                        
                        download_timeout = aiohttp.ClientTimeout(total=120)
                        async with session.get(url, timeout=download_timeout) as img_resp:
                            img_bytes = await img_resp.read()
                        return BufferedInputFile(img_bytes, filename=f"kie_{model}.png"), url
                    
                    elif state == "fail":
                        fail_msg = data.get("failMsg", "Unknown error")
                        raise Exception(f"Kie REJECT: {fail_msg}")
                
                except Exception as loop_e:
                    if "Kie REJECT" in str(loop_e) or "No images" in str(loop_e):
                        raise loop_e
                    print(f"⚠️ Loop Warning: {loop_e}")
                
                await asyncio.sleep(5)
            
    except Exception as e:
        raise e

# ==============================================================================
# 3. ГЛАВНЫЙ РОУТЕР
# ==============================================================================
# 👇 ДОБАВИЛ resolution В АРГУМЕНТЫ
async def generate_image(bot: Bot, prompt: str, image_urls: list = None, is_premium: bool = False, aspect_ratio: str = "1:1", use_pro_model: bool = False, use_nb2_model: bool = False, history: list = None, resolution: str = "1K"):
    return await _run_kie(prompt, image_urls, aspect_ratio, use_pro_model, use_nb2_model, history, resolution)
