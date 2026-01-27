"""
Webhook обработчик для Kling API (генерация видео)
"""

from aiohttp import web
from aiogram import Bot
from app.database import async_session
from app.services.video_service import (
    get_task_by_id,
    update_task_status,
    refund_user_balance,
    download_and_send_video
)
import json


# Настройки
WEBHOOK_PATH = "/kling_webhook"


async def handle_kling_callback(request):
    """
    Обработчик callback от Kling API
    
    Kling отправляет POST запрос с результатами генерации когда задача завершена.
    Структура данных идентична Query Task API response.
    """
    try:
        data = await request.json()
        
        print("=" * 60)
        print("🎬 KLING WEBHOOK RECEIVED")
        print("=" * 60)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        
        # Парсим данные
        code = data.get("code")
        msg = data.get("msg")
        task_data = data.get("data", {})
        
        if code != 200:
            print(f"⚠️ Kling webhook returned non-200 code: {code} - {msg}")
            return web.Response(status=200)  # Всё равно возвращаем 200 чтобы Kling не ретраил
        
        # Извлекаем данные
        task_id = task_data.get("taskId")
        state = task_data.get("state")
        result_json = task_data.get("resultJson")
        fail_code = task_data.get("failCode")
        fail_msg = task_data.get("failMsg")
        
        if not task_id:
            print("⚠️ No taskId in webhook data")
            return web.Response(status=200)
        
        print(f"📝 Task ID: {task_id}")
        print(f"📊 State: {state}")
        
        # Работаем с БД
        async with async_session() as session:
            
            # Получаем задачу из БД
            task = await get_task_by_id(session, task_id)
            
            if not task:
                print(f"⚠️ Task not found in DB: {task_id}")
                return web.Response(status=200)
            
            user_id = task.user_id
            
            # Обрабатываем в зависимости от статуса
            if state == "success":
                print("✅ Generation SUCCESS!")
                
                # Парсим результат
                result_data = json.loads(result_json)
                video_url = result_data["resultUrls"][0]
                
                print(f"🔗 Video URL: {video_url}")
                
                # Обновляем статус в БД
                await update_task_status(
                    session,
                    task_id,
                    status="success",
                    result_video_url=video_url
                )
                
                # Отправляем видео пользователю
                bot_instance = request.app['bot']
                await download_and_send_video(
                    bot=bot_instance,
                    user_id=user_id,
                    video_url=video_url,
                    session=session,
                    task_id=task_id
                )
                
                print(f"✅ Video processing completed for user {user_id}")

            
            elif state == "fail":
                print(f"❌ Generation FAILED!")
                print(f"Fail code: {fail_code}")
                print(f"Fail message: {fail_msg}")
                
                # Обновляем статус в БД
                await update_task_status(
                    session,
                    task_id,
                    status="fail",
                    fail_code=fail_code,
                    fail_message=fail_msg
                )
                
                # Возвращаем бананы
                await refund_user_balance(session, user_id, 10)
                
                # Уведомляем пользователя
                bot_instance = request.app['bot']
                try:
                    await bot_instance.send_message(
                        user_id,
                        "😔 Упс, магия дала сбой.\n\n🍌 Бананы вернулись на баланс\n\nПопробуй другую картинку!",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"⚠️ Failed to notify user: {e}")
                
                print(f"✅ Refund processed for user {user_id}")
                # 📊 Логируем ошибку
                from app.services.admin_logger import log_video_generation_error
                await log_video_generation_error(
                    bot_instance,
                    user_id,
                    None,  # username
                    task_id,
                    fail_msg or fail_code or "Unknown error"
                )
            
            elif state == "waiting":
                print("⏳ Task still waiting... (ignoring)")
            
            else:
                print(f"⚠️ Unknown state: {state}")
        
        print("=" * 60)
        return web.Response(status=200)
    
    except Exception as e:
        print(f"🔴 Kling Webhook Error: {e}")
        import traceback
        traceback.print_exc()
        return web.Response(status=500)


async def start_kling_webhook_server(bot: Bot, port: int = 5002):
    """
    Запускает отдельный webhook сервер для Kling на порту 5002
    
    ИСПОЛЬЗУЙ ЭТОТ ВАРИАНТ ЕСЛИ ХОЧЕШЬ ПОЛНОСТЬЮ ОТДЕЛЬНЫЙ СЕРВЕР
    """
    app = web.Application()
    app['bot'] = bot
    app.router.add_post(WEBHOOK_PATH, handle_kling_callback)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🎬 Kling webhook сервер запущен на порту {port}")
    print(f"🔗 Webhook URL: https://aaa123.site{WEBHOOK_PATH}")