"""
Executor - сервис-слой для выполнения генерации изображений
Разделяет ответственности process_generation на отдельные шаги
"""
import io
import json
import asyncio
from datetime import datetime, timezone

import aiohttp
from PIL import Image
from aiogram import Bot, types
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
from sqlalchemy import update as sa_update

from app.database import async_session
from app.models import BananaTransaction
from app.services.i18n import t
from app.services.user_service import (
    add_history,
    increment_generations_count,
    get_user,
    admin_change_balance,
    track_banana_transaction,
)
from app.services.admin_logger import log_generation, log_referral
from app.services.ai_engine import generate_image
from app.utils.image_utils import smart_compress_image, create_collage
from app.utils.telegram_utils import get_photo_url
from app.keyboards.generation import get_result_kb


# =====================================================================
# ПОДГОТОВКА ИЗОБРАЖЕНИЙ
# =====================================================================

async def build_collage_if_needed(
    bot: Bot,
    user_id: int,
    final_urls: list[str],
    prompt: str,
    use_pro_model: bool,
    use_nb2_model: bool,
    is_blend_mode: bool,
) -> tuple[list[str], str]:
    """
    Создаёт коллаж из нескольких изображений если нужно.
    
    Returns:
        (final_urls, modified_prompt) - обновлённые URL и промпт
    """
    # 🔥 ОПРЕДЕЛЯЕМ СЦЕНАРИЙ: сложный только для Standard (без PRO и NB2)
    is_complex_standard = (not use_pro_model and not use_nb2_model and len(final_urls) >= 2)
    
    # 🔥 ДЕТЕКТОР ЗАДАЧ ТИПА "ЗАМЕНА/ВСТАВКА"
    swap_keywords = [
        'поменя', 'замен', 'положи', 'помести', 'вставь', 'перенес',
        'возьми', 'бери', 'со второ', 'из второ', 'с друго', 'из друго',
        'swap', 'replace', 'put', 'place', 'take from'
    ]
    is_swap_task = any(keyword in prompt.lower() for keyword in swap_keywords)
    
    from app.utils.prompt_utils import is_blend_request
    is_blend_task = is_blend_mode or is_blend_request(prompt)
    
    # 🔥 AUTO-COLLAGE ТОЛЬКО ДЛЯ НЕ-SWAP ЗАДАЧ
    if not (is_complex_standard and len(final_urls) >= 2 and not is_swap_task and not is_blend_task):
        return final_urls, prompt
    
    try:
        print(f"🎨 Создаю коллаж из {len(final_urls)} фото...")
        
        # 1. Скачиваем все изображения
        images = []
        timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            for url in final_urls:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        img_data = await resp.read()
                        img = Image.open(io.BytesIO(img_data))
                        images.append(img)
        
        if len(images) < len(final_urls):
            print(f"⚠️ Не все фото загрузились: {len(images)}/{len(final_urls)}")
        
        if not images:
            print("❌ Ни одно фото не загрузилось для коллажа")
            raise Exception("No images loaded")
        
        # 2. Создаём коллаж (синхронная функция)
        collage = create_collage(images, max_size=1024)
        
        # 3. Конвертируем в bytes
        collage_bytes = io.BytesIO()
        collage.save(collage_bytes, format='PNG')
        collage_bytes.seek(0)
        
        # 4. Загружаем коллаж в Telegram (БЕЗ уведомления)
        temp_msg = await bot.send_photo(
            chat_id=user_id,
            photo=types.BufferedInputFile(collage_bytes.read(), "collage.png"),
            disable_notification=True
        )
        
        # 5. Получаем URL коллажа
        collage_url = await get_photo_url(bot, temp_msg.photo[-1].file_id)
        
        # 6. Удаляем временное сообщение
        try:
            await temp_msg.delete()
        except:
            pass
        
        # 7. ВАЖНО: Заменяем final_urls на коллаж
        final_urls = [collage_url]
        
        # 🔥 МОДИФИЦИРУЕМ ПРОМПТ ДЛЯ КОЛЛАЖА
        if len(images) == 2:
            prompt = f"{prompt}. IMPORTANT: Combine both subjects into a SINGLE unified scene. They should interact naturally, standing together. Do NOT keep the collage structure - merge them into one cohesive image."
        elif len(images) >= 3:
            prompt = f"{prompt}. IMPORTANT: Create a SINGLE unified composition with all {len(images)} subjects together in one scene. Remove the grid layout - merge into one natural photo."
        
        print(f"✅ Коллаж создан: {collage_url[:50]}...")
        print(f"📝 Промпт изменён: {prompt[:150]}...")
        
        return final_urls, prompt
        
    except Exception as e:
        print(f"⚠️ Ошибка создания коллажа: {e}")
        import traceback
        traceback.print_exc()
        # Продолжаем с оригинальными URL (fallback)
        return final_urls, prompt


# =====================================================================
# ВЫПОЛНЕНИЕ ГЕНЕРАЦИИ
# =====================================================================

async def execute_ai_generation(
    bot: Bot,
    prompt: str,
    final_urls: list[str],
    aspect_ratio: str,
    use_pro_model: bool,
    use_nb2_model: bool,
    resolution: str,
    transaction_id: int | None,
) -> tuple:
    """
    Выполняет вызов AI API для генерации изображения.
    
    Returns:
        (result_file, source_url, kie_task_id)
    """
    result_data = await generate_image(
        bot, prompt, final_urls, False,
        aspect_ratio, use_pro_model, use_nb2_model, None, resolution
    )
    
    # Разбор результата
    result_file = None
    source_url = None
    kie_task_id = None
    
    if result_data and isinstance(result_data, tuple):
        if len(result_data) == 3:
            result_file, source_url, kie_task_id = result_data
        else:
            result_file, source_url = result_data
    elif result_data:
        result_file = result_data
    
    # Обновляем post_id в транзакции реальным taskId от KieAI
    if kie_task_id and transaction_id:
        async with async_session() as upd_session:
            await upd_session.execute(
                sa_update(BananaTransaction)
                .where(BananaTransaction.id == transaction_id)
                .values(post_id=kie_task_id)
            )
            await upd_session.commit()
    
    return result_file, source_url, kie_task_id


# =====================================================================
# ОТПРАВКА И СОХРАНЕНИЕ РЕЗУЛЬТАТА
# =====================================================================

async def save_generation_result(
    bot: Bot,
    message: types.Message,
    user_id: int,
    prompt: str,
    final_urls: list[str],
    result_file,
    source_url: str | None,
    balance_left: int,
    cost: int,
    use_pro_model: bool,
    use_nb2_model: bool,
    aspect_ratio: str,
    resolution: str,
    is_blend_mode: bool,
    locale: str,
) -> tuple[int | None, str | None]:
    """
    Отправляет результат пользователю и сохраняет в БД.
    
    Returns:
        (db_id, sent_file_id) - ID записи в БД и file_id отправленного файла
    """
    caption = t("generation.msg.result_caption", locale, balance=balance_left)
    
    # Сжатие для превью
    file_bytes = result_file.data
    compressed_bytes = smart_compress_image(file_bytes)
    
    # Отправка фото
    sent_msg = None
    for attempt in range(3):
        try:
            preview_file = types.BufferedInputFile(
                compressed_bytes, filename="result.png"
            )
            sent_msg = await message.answer_photo(
                preview_file,
                caption=caption,
                parse_mode="HTML",
                request_timeout=300,
            )
            break
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except (TelegramNetworkError, asyncio.TimeoutError) as e:
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
                continue
        except Exception:
            break
    
    # Fallback: отправка документом
    if not sent_msg:
        for attempt in range(2):
            try:
                doc_file = types.BufferedInputFile(
                    file_bytes, filename="result.png"
                )
                sent_msg = await message.answer_document(
                    doc_file,
                    caption=caption,
                    parse_mode="HTML",
                    disable_content_type_detection=True,
                    request_timeout=300,
                )
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except (TelegramNetworkError, asyncio.TimeoutError) as e:
                if attempt < 1:
                    await asyncio.sleep(3)
                    continue
            except Exception:
                break
    
    # Fallback: ссылка на скачивание
    if not sent_msg and source_url:
        try:
            await message.answer(
                t(
                    "generation.msg.download_fallback_link",
                    locale,
                    url=source_url,
                ),
                parse_mode="HTML",
                request_timeout=30,
            )
        except Exception:
            pass
    
    if not sent_msg:
        raise RuntimeError(
            "Telegram delivery timeout while sending generated image"
        )
    
    # Получаем file_id
    sent_file_id = (
        sent_msg.photo[-1].file_id if sent_msg.photo
        else sent_msg.document.file_id
    )
    
    # Логируем генерацию
    await log_generation(
        bot,
        message.chat,
        prompt=prompt,
        model="PRO" if use_pro_model else "NB2" if use_nb2_model else "Standard",
        photo_file_id=sent_file_id
    )
    
    # Формируем метаданные
    meta_data = json.dumps({
        "prompt": prompt,
        "image_urls": final_urls,
        "ratio": aspect_ratio,
        "cost": cost,
        "pro": use_pro_model,
        "nb2": use_nb2_model,
        "resolution": resolution,
        "is_blend_mode": is_blend_mode
    })
    
    # Сохраняем в БД
    async with async_session() as session:
        await add_history(
            session, user_id, "user", prompt,
            has_image=bool(final_urls)
        )
        await increment_generations_count(session, user_id)
        
        model_msg = await add_history(
            session, user_id, "model", meta_data,
            has_image=True,
            file_id=sent_file_id,
            image_url=source_url
        )
        db_id = model_msg.id
    
    # Добавляем кнопки
    if db_id:
        await sent_msg.edit_reply_markup(
            reply_markup=get_result_kb(
                db_id,
                use_pro_model,
                cost,
                is_nb2=use_nb2_model,
                locale=locale,
            )
        )
    
    return db_id, sent_file_id


# =====================================================================
# РЕФЕРАЛЬНЫЕ БОНУСЫ
# =====================================================================

async def award_referral_bonus(
    bot: Bot,
    user_id: int,
    locale: str,
) -> None:
    """
    Начисляет реферальный бонус при первой генерации пользователя.
    Вызывается только если пользователь ранее не делал генераций.
    """
    async with async_session() as session:
        user = await get_user(session, user_id)
        
        # Если это первая генерация пользователя
        if user and not user.first_generation_done:
            user.first_generation_done = True
            await session.commit()
            
            # Если у него есть реферер - начисляем бонус
            if user.referrer_id:
                # 🕒 БЕЗОПАСНАЯ ПРОВЕРКА ДАТЫ (Smart Fix)
                # 1. Берем текущее время (в UTC)
                now_utc = datetime.now(timezone.utc)
                
                # 2. Берем дату регистрации
                reg_date = user.created_at
                
                # 3. ГЛАВНЫЙ ФИКС: Если дата "голая" (без зоны), даем ей UTC
                if reg_date.tzinfo is None:
                    reg_date = reg_date.replace(tzinfo=timezone.utc)
                
                # 4. Теперь вычитаем (ошибки не будет)
                days_since_creation = (now_utc - reg_date).days
                
                if days_since_creation <= 7:  # Только свежие рефералы!
                    try:
                        await admin_change_balance(session, user.referrer_id, 2)
                        await track_banana_transaction(
                            session,
                            user.referrer_id,
                            2,
                            "earned_ref",
                            f"Active referral from {user_id}"
                        )
                        await session.commit()
                        
                        # Получаем обновленный баланс реферера
                        referrer = await get_user(session, user.referrer_id)
                        new_balance = referrer.generations_balance if referrer else 0
                        
                        # Создаем кнопку
                        from aiogram.utils.keyboard import InlineKeyboardBuilder
                        builder = InlineKeyboardBuilder()
                        builder.button(
                            text=t("generation.referral.btn_invite_more", locale),
                            callback_data="goto_free"
                        )
                        
                        # Отправляем уведомление реферу
                        await bot.send_message(
                            user.referrer_id,
                            t("generation.msg.referrer_bonus", locale, new_balance=new_balance),
                            parse_mode="HTML",
                            reply_markup=builder.as_markup()
                        )
                        
                        # Создаем объект для логгера
                        from types import SimpleNamespace
                        new_user_obj = SimpleNamespace(
                            id=user.telegram_id,
                            username=user.username,
                            full_name=user.full_name
                        )
                        await log_referral(bot, user.referrer_id, new_user_obj)
                    except Exception as e:
                        print(f"⚠️ Ошибка начисления реферального бонуса: {e}")
