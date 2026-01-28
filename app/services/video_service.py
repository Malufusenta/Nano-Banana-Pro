"""
Сервис для генерации видео из изображений через Kling API
"""

import types
import aiohttp
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from aiogram import Bot
from aiogram.types import FSInputFile
import os
import tempfile
from app import config
from app.services.admin_logger import log_video_generation_success
from app.models import User
from sqlalchemy import select
from app.models import VideoGenerationTask, User
from app.services.user_service import add_paid_balance
from app.config import KLING_API_KEY, BOT_TOKEN


class KlingAPI:
    """Клиент для работы с Kling API"""
    
    BASE_URL = "https://api.kie.ai/api/v1"
    CREATE_TASK_URL = f"{BASE_URL}/jobs/createTask"
    QUERY_TASK_URL = f"{BASE_URL}/jobs/recordInfo"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    async def create_task(self, image_url: str, webhook_url: str = None) -> dict:
        """
        Создает задачу на генерацию видео
        
        Args:
            image_url: URL изображения
            webhook_url: URL для callback (опционально)
        
        Returns:
            {"success": True, "task_id": "..."} или {"success": False, "error": "..."}
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "kling-2.6/image-to-video",
            "input": {
                "prompt": "Animate this image naturally and smoothly with realistic motion",
                "image_urls": [image_url],
                "sound": False,
                "duration": "5"
            }
        }
        
        # Добавляем webhook если указан
        if webhook_url:
            payload["callBackUrl"] = webhook_url
        
        try:
            connector = aiohttp.TCPConnector(ssl=False)  # ← ДОБАВЬ ЭТУ СТРОКУ
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    self.CREATE_TASK_URL,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"❌ Kling API Error: {response.status} - {error_text}")
                        return {"success": False, "error": f"HTTP {response.status}"}
                    
                    data = await response.json()
                    
                    if data.get("code") != 200:
                        print(f"❌ Kling API returned error: {data}")
                        return {"success": False, "error": data.get("msg", "Unknown error")}
                    
                    task_id = data["data"]["taskId"]
                    print(f"✅ Kling task created: {task_id}")
                    
                    return {"success": True, "task_id": task_id}
        
        except asyncio.TimeoutError:
            print("❌ Kling API timeout")
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            print(f"❌ Kling API exception: {e}")
            return {"success": False, "error": str(e)}
    
    async def check_status(self, task_id: str) -> dict:
        """
        Проверяет статус задачи
        
        Returns:
            {
                "state": "waiting" | "success" | "fail",
                "result_url": "..." (если success),
                "fail_code": "..." (если fail),
                "fail_message": "..." (если fail)
            }
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        params = {"taskId": task_id}
        
        try:
            connector = aiohttp.TCPConnector(ssl=False)  # ← ДОБАВЬ
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    self.QUERY_TASK_URL,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    
                    if response.status != 200:
                        return {"state": "error", "fail_message": f"HTTP {response.status}"}
                    
                    data = await response.json()
                    
                    if data.get("code") != 200:
                        return {"state": "error", "fail_message": data.get("msg", "Unknown error")}
                    
                    task_data = data["data"]
                    state = task_data["state"]
                    
                    result = {"state": state}
                    
                    if state == "success":
                        import json
                        result_json = json.loads(task_data["resultJson"])
                        result["result_url"] = result_json["resultUrls"][0]
                    
                    elif state == "fail":
                        result["fail_code"] = task_data.get("failCode")
                        result["fail_message"] = task_data.get("failMsg")
                    
                    return result
        
        except Exception as e:
            print(f"❌ Check status error: {e}")
            return {"state": "error", "fail_message": str(e)}


# Инициализируем клиент
kling_api = KlingAPI(KLING_API_KEY)


async def get_telegram_file_url(bot: Bot, file_id: str) -> str:
    """
    Получает прямую ссылку на файл из Telegram
    
    Returns:
        URL файла или None при ошибке
    """
    try:
        file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        return file_url
    except Exception as e:
        print(f"❌ Error getting Telegram file URL: {e}")
        return None


async def create_video_generation_task(
    session: AsyncSession,
    user_id: int,
    image_file_id: str,
    bot: Bot,
    webhook_url: str = None
) -> dict:
    """
    Создает задачу на генерацию видео и сохраняет в БД
    
    Args:
        session: Database session
        user_id: Telegram ID пользователя
        image_file_id: file_id картинки из Telegram
        bot: Aiogram Bot instance
        webhook_url: URL для webhook callback
    
    Returns:
        {"success": True, "task_id": "..."} или {"success": False, "error": "..."}
    """
    
    # 1. Получаем URL картинки из Telegram
    image_url = await get_telegram_file_url(bot, image_file_id)
    
    if not image_url:
        return {"success": False, "error": "Failed to get image URL from Telegram"}
    
    # 2. Создаем задачу в Kling API
    result = await kling_api.create_task(image_url, webhook_url)
    
    if not result["success"]:
        return result
    
    task_id = result["task_id"]
    
    # 3. Сохраняем в БД
    try:
        task = VideoGenerationTask(
            user_id=user_id,
            task_id=task_id,
            source_image_file_id=image_file_id,
            source_image_url=image_url,
            status="waiting",
            cost=10
        )
        session.add(task)
        await session.commit()
        
        print(f"✅ Video task saved to DB: user={user_id}, task_id={task_id}")
        
        return {"success": True, "task_id": task_id}
    
    except Exception as e:
        print(f"❌ Error saving video task to DB: {e}")
        await session.rollback()
        return {"success": False, "error": f"Database error: {e}"}


async def get_task_by_id(session: AsyncSession, task_id: str) -> VideoGenerationTask | None:
    """Получает задачу из БД по task_id"""
    result = await session.execute(
        select(VideoGenerationTask).where(VideoGenerationTask.task_id == task_id)
    )
    return result.scalar_one_or_none()


async def update_task_status(
    session: AsyncSession,
    task_id: str,
    status: str,
    result_video_url: str = None,
    fail_code: str = None,
    fail_message: str = None
):
    """Обновляет статус задачи в БД"""
    task = await get_task_by_id(session, task_id)
    
    if not task:
        print(f"⚠️ Task not found in DB: {task_id}")
        return
    
    task.status = status
    task.completed_at = datetime.now()
    
    if result_video_url:
        task.result_video_url = result_video_url
    
    if fail_code:
        task.fail_code = fail_code
    
    if fail_message:
        task.fail_message = fail_message
    
    await session.commit()
    print(f"✅ Task {task_id} updated: status={status}")


async def refund_user_balance(session: AsyncSession, user_id: int, amount: int):
    """Возвращает бананы пользователю при ошибке"""
    
    # Проверяем задачу - возврат уже был?
    result = await session.execute(
        select(VideoGenerationTask)
        .where(
            VideoGenerationTask.user_id == user_id,
            VideoGenerationTask.status == "fail",
            VideoGenerationTask.refunded == False
        )
        .order_by(VideoGenerationTask.created_at.desc())
        .limit(1)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        print(f"⚠️ No failed task found for refund: user={user_id}")
        return
    
    # Возвращаем на платный баланс (потому что списывали с платного)
    await add_paid_balance(session, user_id, amount)
    
    # Помечаем что возврат сделан
    task.refunded = True
    await session.commit()
    
    print(f"✅ Refunded {amount} bananas to user {user_id}")


async def download_and_send_video(
    bot: Bot,
    user_id: int,
    video_url: str,
    session: AsyncSession,
    task_id: str
):
    """
    Скачивает видео и отправляет пользователю
    """
    temp_file = None
    
    try:
        # 1. Скачиваем видео во временный файл
        print(f"📥 Downloading video from: {video_url}")
        
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as http_session:
            async with http_session.get(video_url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                if response.status != 200:
                    raise Exception(f"Failed to download video: HTTP {response.status}")
                
                # Создаем временный файл
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                    temp_file = tmp.name
                    
                    # Скачиваем чанками
                    while True:
                        chunk = await response.content.read(8192)
                        if not chunk:
                            break
                        tmp.write(chunk)
        
        print(f"✅ Video downloaded: {temp_file}")
        
        # 2. Получаем баланс
        from app.services.user_service import get_user_balance
        balance = await get_user_balance(session, user_id)
        
        caption = (
            f"🎬 <b>Твое видео готово!</b>\n\n"
            f"🔋 Осталось: <b>{balance}</b> 🍌\n\n"
            f"Сгенерировано в @nan0banana_bot"
        )
        
        # 3. Добавляем кнопку
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from app import config

        builder = InlineKeyboardBuilder()
        builder.button(text=f"🔄 Ещё раз ({config.COST_VIDEO}🍌)", callback_data=f"reanimate_{task_id}")
        builder.button(text="📂 Скачать без сжатия", callback_data=f"download_video_{task_id}")
        builder.adjust(1)  # По одной кнопке в ряд
        
        # 4. Отправляем в Telegram (КАК РАБОТАЛО!)
        video_input = FSInputFile(temp_file)
        
        message = await bot.send_video(
            chat_id=user_id,
            video=video_input,
            caption=caption,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
            request_timeout=300  # 5 минут
        )
        
        # 5. Сохраняем file_id видео в БД
        video_file_id = message.video.file_id
        
        task = await get_task_by_id(session, task_id)
        if task:
            task.result_video_file_id = video_file_id
            await session.commit()
        
        print(f"✅ Video sent to user {user_id}, file_id={video_file_id}")
        
        # 6. Логируем успех
        from app.services.admin_logger import log_video_generation_success
        from app.models import User
        from sqlalchemy import select
        
        user_result = await session.execute(select(User).where(User.telegram_id == user_id))
        db_user = user_result.scalar_one_or_none()
        username = db_user.username if db_user else None
        
        await log_video_generation_success(
            bot,
            user_id,
            username,
            video_file_id,
            task_id
        )
    
    except Exception as e:
        print(f"❌ Error sending video: {e}")
        
        # Уведомляем пользователя об ошибке
        try:
            await bot.send_message(
                user_id,
                "😔 Упс, не удалось отправить видео.\n\nБананы вернулись на баланс 🍌",
                parse_mode="HTML"
            )
        except:
            pass
        
        # Возвращаем средства
        await refund_user_balance(session, user_id, 10)
    
    finally:
        # Удаляем временный файл
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
                print(f"🗑 Temp file removed: {temp_file}")
            except:
                pass