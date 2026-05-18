"""
Видео-генерация - анимация изображений
"""
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database import async_session
from app.services.user_service import (
    check_and_deduct_balance,
    get_user_balance,
    admin_change_balance,
    get_history_message_by_id,
)
from app.services.i18n import resolve_locale, t
from app.utils.telegram_locale import effective_locale
from app import config

# Импортируем из родительского модуля
from app.handlers.generation_states import GenState
from app.handlers.generation_flow.preflight import get_smart_alert_message, _preflight_locale

router = Router()


# =====================================================================
# ВИДЕО ФУНКЦИИ
# =====================================================================

async def send_video_offer_message(
    message: types.Message,
    state: FSMContext,
    has_photo: bool = False,
    photo_file_id: str = None,
    locale: str | None = None,
):
    """
    Отправляет предложение создать видео
    """
    bot = message.bot
    async with async_session() as session:
        locale = await effective_locale(bot, message, message.chat.id, locale, session=session)
    builder = InlineKeyboardBuilder()
    
    # Добавляем file_id в callback_data если есть
    # Всегда используем просто "video_start"
    # file_id сохраним в state
    callback_data = "video_start"
    
    builder.button(
        text=t("generation.video.btn_animate", locale, cost=config.COST_VIDEO),
        callback_data=callback_data,
    )
    builder.button(text=t("common.cancel_button", locale), callback_data="video_cancel")
    builder.adjust(1)
    
    text = t("generation.msg.video_offer", locale, cost=config.COST_VIDEO)
    
    if has_photo and photo_file_id:
        await state.update_data(pending_video_photo=photo_file_id)
    
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


async def offer_video_if_requested(
    message: types.Message,
    state: FSMContext,
    text: str | None,
    *,
    photo_file_id: str | None = None,
    clear_state: bool = True,
    locale: str | None = None,
) -> bool:
    """
    Если в тексте запрос на видео — показывает оффер и возвращает True.
    """
    from app.utils.video_keywords import is_video_request

    if not is_video_request(text):
        return False

    if clear_state:
        await state.clear()

    await send_video_offer_message(
        message,
        state,
        has_photo=bool(photo_file_id),
        photo_file_id=photo_file_id,
        locale=locale,
    )
    return True


async def process_video_generation(
    message: types.Message,
    user_id: int,
    photo_file_id: str,
    state: FSMContext,
    from_result_button: bool = False,
    username: str = None,
    locale: str | None = None,
):
    """
    Основная функция обработки генерации видео
    """
    from app.services.video_service import create_video_generation_task

    COST = config.COST_VIDEO
    bot = message.bot

    # Проверка баланса
    async with async_session() as session:
        locale = await effective_locale(bot, message, user_id, locale, session=session)
        has_balance, _ = await check_and_deduct_balance(session, user_id, amount=COST)
        balance_left = await get_user_balance(session, user_id)

        if not has_balance:
            alert_text, alert_kb = await get_smart_alert_message(session, user_id, balance_left, COST, locale)
            await message.answer(alert_text, reply_markup=alert_kb.as_markup(), parse_mode="HTML")
            return
    
    # Списали деньги - запускаем генерацию
    if from_result_button:
        wait_text = t("generation.msg.video_wait_kling", locale)
    else:
        wait_text = t("generation.msg.video_wait_simple", locale)
    
    wait_msg = await message.answer(wait_text, parse_mode="HTML")
    
    # Webhook URL (обязательно с путём /kling_webhook — см. app/webhook_server.py)
    webhook_url = f"{config.KLING_WEBHOOK_BASE_URL}{config.KLING_WEBHOOK_PATH}"
    try:
        # Создаем задачу
        async with async_session() as session:
            result = await create_video_generation_task(
                session=session,
                user_id=user_id,
                image_file_id=photo_file_id,
                bot=message.bot,
                webhook_url=webhook_url
            )
        
        if result["success"]:
            print(f"✅ Video task created: {result['task_id']}")
            
            # 📊 Логируем запуск
            from app.services.admin_logger import log_video_generation_start
            await log_video_generation_start(
                message.bot,
                user_id,
                username,
                COST,
                result['task_id']
            )
        else:
            # Ошибка создания задачи - возвращаем деньги
            async with async_session() as session:
                await admin_change_balance(session, user_id, COST)
            from app.services.admin_logger import log_banana_refund
            await log_banana_refund(message.bot, user_id, username, COST, "Сервис генерации видео недоступен")
            
            await wait_msg.edit_text(
                t("generation.msg.video_service_down", locale, cost=COST),
                parse_mode="HTML",
            )
    
    except Exception as e:
        print(f"❌ Ошибка process_video_generation: {e}")
        
        # Возвращаем деньги
        async with async_session() as session:
            await admin_change_balance(session, user_id, COST)
        from app.services.admin_logger import log_banana_refund
        await log_banana_refund(message.bot, user_id, username, COST, f"Ошибка запуска видео: {str(e)[:50]}")
        
        await wait_msg.edit_text(
            t("generation.msg.video_crash_refund", locale, cost=COST),
            parse_mode="HTML",
        )
    
    finally:
        # Очищаем pending_video_photo только после успешного старта
        if 'result' in locals() and result.get("success"):
            await state.update_data(pending_video_photo=None)


# =====================================================================
# КОЛБЕКИ ВИДЕО
# =====================================================================

@router.callback_query(F.data.startswith("video_start"))
async def cb_video_start(callback: types.CallbackQuery, state: FSMContext):
    """Начало процесса создания видео"""
    
    # Отвечаем на callback СРАЗУ (иначе устареет)
    try:
        await callback.answer()
    except:
        pass  # Игнорируем если уже устарел
    
    # Достаем file_id из state
    data = await state.get_data()
    photo_file_id = data.get("pending_video_photo")
    
    if photo_file_id:
        # Фото уже есть - переходим к генерации
        await process_video_generation(
            callback.message,
            callback.from_user.id,
            photo_file_id,
            state,
            username=callback.from_user.username,
            locale=_preflight_locale(callback.from_user),
        )
    else:
        # Фото нет - просим прислать
        await state.set_state(GenState.waiting_for_video_source)
        locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
        
        builder = InlineKeyboardBuilder()
        builder.button(text=t("common.cancel_button", locale), callback_data="video_cancel")
        
        await callback.message.answer(
            t("generation.msg.video_need_photo", locale),
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "video_cancel")
async def cb_video_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Отмена создания видео"""
    await state.clear()
    await state.update_data(pending_video_photo=None)
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    
    await callback.message.answer(
        t("generation.msg.video_cancelled", locale),
    )
    
    await callback.answer()


@router.message(GenState.waiting_for_video_source, F.photo)
async def handle_video_source_photo(message: types.Message, state: FSMContext):
    """Обработка фото для генерации видео"""
    photo_file_id = message.photo[-1].file_id
    await state.clear()

    await process_video_generation(
        message,
        message.from_user.id,
        photo_file_id,
        state,
        username=message.from_user.username
    )


@router.callback_query(F.data.startswith("animate_"))
async def cb_animate_result(callback: types.CallbackQuery, state: FSMContext):
    """Оживление существующего результата"""
    await callback.answer()
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    
    try:
        db_id = int(callback.data.split("_")[1])
        
        async with async_session() as session:
            history_item = await get_history_message_by_id(session, db_id)
        
        if not history_item or not history_item.file_id:
            await callback.answer(t("generation.alert.source_not_found", locale), show_alert=True)
            return
        
        photo_file_id = history_item.file_id
        await process_video_generation(
            callback.message,
            callback.from_user.id,
            photo_file_id,
            state,
            from_result_button=True,
            username=callback.from_user.username,
            locale=locale,
        )
    except Exception as e:
        print(f"❌ Ошибка animate: {e}")
        await callback.answer(t("generation.alert.video_animate_failed", locale), show_alert=True)


@router.callback_query(F.data.startswith("reanimate_"))
async def cb_reanimate_video(callback: types.CallbackQuery, state: FSMContext):
    """Повторная генерация видео (ещё раз)"""
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    await callback.answer(t("generation.alert.reroll_starting", locale), show_alert=False)
    
    try:
        task_id = callback.data.split("_", 1)[1]
        
        # Получаем исходную задачу из БД
        async with async_session() as session:
            from app.services.video_service import get_task_by_id
            original_task = await get_task_by_id(session, task_id)
        
        if not original_task or not original_task.source_image_file_id:
            await callback.answer(t("generation.alert.source_not_found", locale), show_alert=True)
            return
        
        # Запускаем новую генерацию с тем же фото
        await process_video_generation(
            callback.message,
            callback.from_user.id,
            original_task.source_image_file_id,
            state,
            username=callback.from_user.username,
            locale=locale,
        )
        
    except Exception as e:
        print(f"❌ Ошибка reanimate: {e}")
        await callback.answer(t("generation.alert.reanimate_failed", locale), show_alert=True)
