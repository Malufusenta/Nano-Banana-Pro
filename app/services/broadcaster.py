"""
Broadcaster - Система массовых рассылок

Особенности:
- Rate limiting (25 сообщений/сек)
- Обработка ошибок (blocked users)
- Отчётность админу
- Фоновое выполнение
"""

import asyncio
from datetime import datetime
from sqlalchemy import select, update
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database import async_session
from app.models import User, Broadcast


async def start_broadcast(bot: Bot, broadcast_id: int, admin_id: int):
    """
    Запускает рассылку в фоне
    
    Параметры:
    - bot: экземпляр бота
    - broadcast_id: ID рассылки из БД
    - admin_id: ID админа (для отчёта)
    """

    from datetime import datetime, timedelta

    # Получаем данные рассылки и список пользователей
    async with async_session() as session:
        # 1. Получаем рассылку
        result = await session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )
        broadcast = result.scalar_one_or_none()
        if not broadcast:
            return
                
        # 2. Получаем активных пользователей (исключаем новичков и заблокировавших)
        cutoff_time = datetime.now() - timedelta(hours=24)
        
        users_result = await session.execute(
            select(User.telegram_id)
            .where(
                User.created_at < cutoff_time,
                User.is_blocked == False  # ✅ Только активные
            )
        )
        user_ids = [row[0] for row in users_result.fetchall()]

    print(f"📊 Broadcast #{broadcast_id}: Отправка {len(user_ids)} пользователям (исключены новички)")
    
    # Счётчики
    sent = 0
    delivered = 0
    blocked = 0
    
    start_time = datetime.now()
    
    # Парсим кнопки
    keyboard = None
    if broadcast.buttons:
        import json
        buttons = json.loads(broadcast.buttons)
        builder = InlineKeyboardBuilder()
        
        for btn in buttons:
            if btn['type'] == 'url':
                builder.button(text=btn['text'], url=btn['data'])
            else:
                # Для callback передаём broadcast_id чтобы знать какой промпт использовать
                builder.button(
                    text=btn['text'], 
                    callback_data=f"bc_{broadcast_id}"
                )
        
        builder.adjust(1)
        keyboard = builder.as_markup()
    
    # Рассылаем
    for user_id in user_ids:
        try:
            # Rate limiting: 25 сообщений в секунду
            if sent > 0 and sent % 25 == 0:
                await asyncio.sleep(1)
            
            # Отправляем сообщение
            if broadcast.media_type == "photo":
                try: 
                    file_ids = json.loads(broadcast.media_file_ids)
                except json.JSONDecodeError:
                    import ast
                    file_ids = ast.literal_eval(broadcast.media_file_ids)
                except:
                    print(f"❌ Failed to parse media_file_ids: {broadcast.media_file_ids}")
                    continue  # Пропускаем этого юзера  

                await bot.send_photo(
                    chat_id=user_id,
                    photo=file_ids[0],
                    caption=broadcast.message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )

            elif broadcast.media_type == "video":  # ✅ НОВОЕ
                try: 
                    file_ids = json.loads(broadcast.media_file_ids)
                except json.JSONDecodeError:
                    import ast
                    file_ids = ast.literal_eval(broadcast.media_file_ids)
                except:
                    print(f"❌ Failed to parse media_file_ids: {broadcast.media_file_ids}")
                    continue

                await bot.send_video(
                    chat_id=user_id,
                    video=file_ids[0],
                    caption=broadcast.message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )

            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=broadcast.message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            
            sent += 1
            delivered += 1
            
        except TelegramForbiddenError:
            # Пользователь заблокировал бота
            blocked += 1
            sent += 1
            
            # ✅ Помечаем в БД
            try:
                async with async_session() as session:
                    await session.execute(
                        update(User).where(User.telegram_id == user_id).values(is_blocked=True)
                    )
                    await session.commit()
            except Exception as db_error:
                print(f"⚠️ Failed to mark user {user_id} as blocked: {db_error}")
            
        except TelegramRetryAfter as e:
            print(f"⏳ Broadcast FloodWait: ждём {e.retry_after} сек")
            await asyncio.sleep(e.retry_after)
            try:
                if broadcast.media_type == "photo":
                    await bot.send_photo(chat_id=user_id, photo=file_ids[0], caption=broadcast.message_text, reply_markup=keyboard, parse_mode="HTML")
                elif broadcast.media_type == "video":
                    await bot.send_video(chat_id=user_id, video=file_ids[0], caption=broadcast.message_text, reply_markup=keyboard, parse_mode="HTML")
                else:
                    await bot.send_message(chat_id=user_id, text=broadcast.message_text, reply_markup=keyboard, parse_mode="HTML")
                delivered += 1
            except Exception:
                pass
            sent += 1

        except Exception as e:
            # Другие ошибки (чат не найден, etc)
            print(f"⚠️ Broadcast error for user {user_id}: {e}")
            sent += 1
    
    # Обновляем статистику в БД
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    async with async_session() as session:
        await session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status="completed",
                sent_count=sent,
                delivered_count=delivered,
                blocked_count=blocked,
                started_at=start_time,
                completed_at=end_time
            )
        )
        await session.commit()
    
    # Отправляем отчёт админу
    report = (
        f"📊 <b>Рассылка #{broadcast_id} завершена</b>\n\n"
        f"✅ Всего отправлено: {sent}\n"
        f"📬 Доставлено: {delivered}\n"
        f"🚫 Заблокировали бота: {blocked}\n"
        f"⏱ Время выполнения: {int(duration // 60)} мин {int(duration % 60)} сек"
    )
    
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=report,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"⚠️ Failed to send report to admin: {e}")