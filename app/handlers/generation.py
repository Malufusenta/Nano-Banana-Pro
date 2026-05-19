import json
import io
import unicodedata
from PIL import Image
from aiogram import Router, types, F, Bot
from aiogram.filters import StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatAction
from aiogram import html
import aiohttp
from app.services.admin_logger import (
    log_content_filter,
    log_duplicate_photo_interceptor,
    log_error,
    log_generation,
    log_lazy_prompt_interceptor,
    log_referral,
    log_security_ban,
)
from app.models import User, Broadcast, PostConfig, BananaTransaction
from app.middlewares.content_filter import ContentFilter, FilterMode, get_filter_message, log_nsfw_to_chat
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from app.database import async_session
from app.services.user_service import (
    check_and_deduct_balance, get_user_balance, is_user_premium, 
    add_history, clear_history, get_history_message_by_id, get_dialog_context,
    start_generation_task, finish_generation_task, admin_change_balance,
    get_user_model_preference, set_user_model_preference, has_user_purchased, get_user,
    track_banana_transaction, increment_generations_count,
)
from app.services.ai_engine import generate_image
from app.services.image_hash_service import (
    ImageHashError,
    apply_duplicate_penalty,
    compute_phashes_for_urls,
    find_recent_duplicate_hash,
    should_run_image_hash_check,
    store_image_hashes,
)
from app.utils import prompts
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, update, select
from app.utils.prompt_validator import is_lazy_prompt
from app.services.i18n import resolve_locale, t
from app.utils.telegram_locale import effective_locale
from app.handlers.payment import get_banana_label
from app import config
import asyncio
import logging
from aiogram.exceptions import (
    TelegramRetryAfter,
    TelegramForbiddenError,
    TelegramNetworkError,
)

# Утилиты для работы с изображениями и Telegram
from app.utils.image_utils import smart_compress_image, create_collage, normalize_image_urls
from app.utils.telegram_utils import get_photo_url, is_image_document

# Клавиатуры
from app.keyboards.generation import get_preflight_kb, get_ratio_kb, get_result_kb, get_cancel_kb

# Калькулятор стоимости и обработка ошибок
from app.services.generation import calc_cost, get_kie_credits
from app.services.generation.model_description import get_model_description
from app.utils.error_handler import handle_generation_error, handle_null_result_error
from app.handlers.generation_states import GenState
from app.utils.prompt_utils import is_blend_request

# Импорт функций из generation_flow для использования в других модулях
from app.handlers.generation_flow.preflight import (
    start_preflight_check,
    get_smart_alert_message,
    compose_preflight_message_html,
    compose_preflight_scenario_ready_html,
    _preflight_locale,
)
from app.handlers.generation_flow.video import (
    offer_video_if_requested,
    send_video_offer_message,
    process_video_generation,
)
logger = logging.getLogger(__name__)
content_filter = ContentFilter(
    FilterMode[config.FILTER_MODE.upper()]  # "shadow" -> FilterMode.SHADOW
)

router = Router()

COMPLAINT_INSTRUCTION_PHOTO = "AgACAgIAAxkBAALT5Wljc3V_Fhya4RZZ0xab7eXhFtE-AAIZDGsbxiYgS76CBLyQRXTjAQADAgADeQADOAQ"  # 👈 Вставь свой file_id


# 👇 ЗАМЕНИТЬ ВЕСЬ СПИСОК IGNORED_TEXTS НА ЭТОТ:
IGNORED_TEXTS = [
    # Keep only invariant command-style markers.
    "/start", "/help", "/admin", "/stats", "/clear", "/admin_scenarios",
    "/profile", "/free", "/about", "/support", "/guide", "/proxy", "/buy", "/new"
]

PARAM_USER_VALUE_MAX_LEN = 500


def _first_photo_file_id(messages: list[types.Message]) -> str | None:
    for msg in messages:
        if msg.photo:
            return msg.photo[-1].file_id
    return None


async def _save_pending_photo(
    state: FSMContext,
    image_urls: list[str],
    photo_file_id: str | None,
) -> None:
    payload = {"pending_image_urls": image_urls}
    if photo_file_id:
        payload["pending_photo_file_id"] = photo_file_id
    await state.update_data(**payload)


def apply_value_to_main_prompt(main_prompt: str, user_value: str) -> str:
    v = (user_value or "").strip()
    if len(v) > PARAM_USER_VALUE_MAX_LEN:
        v = v[:PARAM_USER_VALUE_MAX_LEN]
    return (main_prompt or "").replace("{value}", v)


async def send_param_prompt_text_intro(
    bot: Bot,
    chat_id: int,
    question: str,
    locale: str = "en",
    reply_markup: types.ReplyKeyboardMarkup | types.ReplyKeyboardRemove | None = None,
) -> None:
    await bot.send_message(
        chat_id,
        t("prompt.intro_applied", locale),
        parse_mode="HTML",
    )
    await asyncio.sleep(0.8)
    safe_q = html.quote(question)
    await bot.send_message(
        chat_id,
        f"❓ <i>{safe_q}</i>\n\n{t('prompt.answer_here', locale)}",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def send_param_prompt_photo_before_text_error(
    bot: Bot, chat_id: int, question: str, *, locale: str
) -> None:
    await bot.send_message(
        chat_id,
        t("prompt.photo_before_text", locale),
        parse_mode="HTML",
    )
    await asyncio.sleep(0.8)
    safe_q = html.quote(question)
    await bot.send_message(
        chat_id,
        f"❓ <i>{safe_q}</i>\n\n{t('prompt.answer_here_simple', locale)}",
        parse_mode="HTML",
    )




async def enter_broadcast_generation_preflight(
    message: types.Message,
    state: FSMContext,
    bot: Bot,
    *,
    prompt: str,
    ratio: str,
    model: str,
    file_id: str | None = None,
    file_ids: list[str] | None = None,
) -> None:
    """Собраны промпт + фото (рассылка/post link) → preflight как при обычной отправке фото."""
    locale_pf = _preflight_locale(message.from_user)
    ids = list(file_ids or [])
    if file_id:
        ids.append(file_id)
    image_urls: list[str] = []
    for fid in ids:
        url = await get_photo_url(bot, fid)
        if url:
            image_urls.append(url)
    if not image_urls:
        await message.answer(t("generation.msg.photo_fetch_failed", locale_pf))
        return
    await state.update_data(
        from_broadcast=False,
        broadcast_prompt=None,
        broadcast_ratio=None,
        pending_param_photo_file_id=None,
        pending_param_photo_file_ids=None,
    )
    await state.update_data(
        pf_prompt=prompt,
        pf_image_urls=image_urls,
        pf_ratio=ratio,
        pf_model=model,
        is_broadcast_gen=True,
        no_standard_model=True,
    )
    await state.set_state(GenState.preflight_check)
    locale = _preflight_locale(message.from_user)
    text = compose_preflight_message_html(
        locale,
        prompt_raw="",
        cost=0,
        model=model,
        has_photo=True,
        is_edit_mode=False,
        is_broadcast=True,
        use_settings_header=False,
    )
    await message.answer(
        text,
        reply_markup=get_preflight_kb(model, ratio, "1k", locale),
        parse_mode="HTML",
    )


def get_duplicate_photo_block_kb(locale: str):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t("generation.abuse.duplicate_photo_button", locale),
        callback_data="goto_shop",
    )
    builder.adjust(1)
    return builder.as_markup()

# =====================================================================
# 🔥 COMPLAINT FILTER - Обработка жалоб на сходство
# =====================================================================

async def send_complaint_instruction(message: types.Message):
    """
    Отправляет инструкцию при срабатывании фильтра жалоб
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    builder = InlineKeyboardBuilder()
    builder.button(text=t("generation.complaint.btn_retry", locale), callback_data="retry_correct_flow")
    builder.button(text=t("generation.complaint.btn_not_now", locale), callback_data="complaint_not_now")
    builder.adjust(1)
    
    instruction_text = t("generation.complaint.instruction", locale)
    
    try:
        await message.answer_photo(
            photo=COMPLAINT_INSTRUCTION_PHOTO,
            caption=instruction_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"⚠️ Ошибка отправки фото: {e}")
        # Fallback: отправляем без фото
        await message.answer(
            instruction_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

    
    # Логируем в админский канал
    from app.services.admin_logger import log_complaint_filter
    await log_complaint_filter(
        message.bot,
        message.from_user.id,
        message.from_user.username,
        message.text or message.caption or "",
    )

# =====================================================================
# 🎛 КЛАВИАТУРЫ (перенесены в app/keyboards/generation/)
# =====================================================================

# =====================================================================
# 🛫 ПРЕДПОЛЕТНЫЙ ЧЕК (перенесено в generation_flow/preflight.py)
# =====================================================================

# =====================================================================
# 📸 IMAGE FLOW
# =====================================================================

def get_categories_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🖼 Заменить объект", callback_data="cat_replace")
    builder.button(text="✨ AI-Фотосессия", callback_data="cat_photo")
    builder.button(text="🎭 В Аниме", callback_data="cat_anime")
    builder.button(text="🚗 Разбить тачку", callback_data="cat_crash")
    builder.button(text="🏚 Бомж в квартире", callback_data="cat_homeless")
    builder.button(text="🔥 Пожар", callback_data="cat_fire")
    builder.button(text="🎨 Свободный режим", callback_data="cat_free")
    builder.adjust(1, 2, 2, 2)
    return builder.as_markup()

# =====================================================================
# 🛫 ПРЕДПОЛЕТНЫЙ ЧЕК (перенесено в generation_flow/preflight.py)
# =====================================================================

# =====================================================================
# ВХОДНЫЕ ТОЧКИ
# =====================================================================
@router.message(
    F.chat.type == "private",
    F.media_group_id,
    StateFilter(
        GenState.free_mode,
        None,
        GenState.preflight_check,
        GenState.selecting_ratio,
        GenState.retry_waiting_photos,
        GenState.waiting_for_prompt_photo,
        GenState.waiting_for_prompt_text,
    ),
)
async def handle_album_input(message: types.Message, state: FSMContext, bot: Bot, album: list[types.Message] = None):
    """Обработка альбомов (2-10 фото)"""

    messages = album if album else [message]
    current_state = await state.get_state()

    # Post link / рассылка: альбом до ответа на вопрос с {value}
    if current_state == GenState.waiting_for_prompt_text.state:
        data = await state.get_data()
        q = (data.get("param_question_text") or "").strip()
        locale = _preflight_locale(message.from_user)
        if not q:
            await message.answer(t("prompt.answer_first", locale))
            return
        file_ids = []
        for msg in messages:
            if msg.photo:
                file_ids.append(msg.photo[-1].file_id)
        if not file_ids:
            await message.answer(t("generation.msg.photo_fetch_failed", locale))
            return
        await state.update_data(
            pending_param_photo_file_ids=file_ids,
            pending_param_photo_file_id=file_ids[0],
        )
        await send_param_prompt_photo_before_text_error(
            message.bot, message.chat.id, q, locale=locale
        )
        return

    # 🔥 СОХРАНЯЕМ BROADCAST ДАННЫЕ ДО ОЧИСТКИ STATE 🔥
    data = await state.get_data()
    broadcast_prompt = data.get('broadcast_prompt')
    broadcast_ratio = data.get('broadcast_ratio', '1:1')
    broadcast_model = data.get('broadcast_model', 'standard')  # 👈 СОХРАНЯЕМ МОДЕЛЬ
    is_from_broadcast = data.get('from_broadcast', False)
    force_pro_mode = data.get('force_pro_mode', False)  # 👈 СОХРАНЯЕМ ФЛАГ

        # 🔥 ДОБАВЬ ЭТИ СТРОКИ ДЛЯ РЕКЛАМНЫХ СЦЕНАРИЕВ:
    ad_scenario_prompt = data.get('ad_scenario_prompt')
    ad_scenario_ratio = data.get('ad_scenario_ratio', '1:1')
    ad_scenario_model = data.get('ad_scenario_model', 'standard')
    is_from_ad_scenario = data.get('from_ad_scenario', False)

    
    await state.clear()  # Очищаем state

        # 🔥 ВОССТАНАВЛИВАЕМ force_pro_mode ЕСЛИ БЫЛ
    if force_pro_mode:
        await state.update_data(force_pro_mode=True)

    count = len(messages)
    locale = _preflight_locale(message.from_user)
    
    if count > 4:
        await message.answer(t("generation.msg.album_too_many", locale), parse_mode="HTML")
        return
    
    image_urls = []
    full_caption = ""
    first_photo_file_id = None

    for msg in messages:
        if msg.photo:
            if first_photo_file_id is None:
                first_photo_file_id = msg.photo[-1].file_id
            url = await get_photo_url(bot, msg.photo[-1].file_id)
            if url:
                image_urls.append(url)
        if msg.caption and not full_caption:
            full_caption = msg.caption
    
    if not image_urls:
        await message.answer(t("generation.msg.photo_fetch_failed", locale))
        return
    
    # ЕСЛИ ЭТО РЕКЛАМНЫЙ СЦЕНАРИЙ
    if is_from_ad_scenario and ad_scenario_prompt:
        await state.update_data(
            pf_prompt=ad_scenario_prompt,
            pf_image_urls=image_urls,
            pf_ratio=ad_scenario_ratio,
            pf_model=ad_scenario_model,
            pf_quality="hd" if ad_scenario_model == "nb2" else "2k",
            is_ad_scenario_gen=True
        )
        await state.set_state(GenState.preflight_check)

        locale = _preflight_locale(message.from_user)
        text = compose_preflight_scenario_ready_html(locale)
        await message.answer(
            text,
            reply_markup=get_preflight_kb(ad_scenario_model, ad_scenario_ratio, "2k", locale),
            parse_mode="HTML",
        )
        return

    # 🔥 ЕСЛИ ЭТО BROADCAST - ИСПОЛЬЗУЕМ СОХРАНЁННЫЙ ПРОМПТ 🔥
    if is_from_broadcast and broadcast_prompt:
        await state.update_data(
            pf_prompt=broadcast_prompt,
            pf_image_urls=image_urls,
            pf_ratio=broadcast_ratio,
            pf_model=broadcast_model,
            pf_quality="hd" if broadcast_model == "nb2" else "2k",
            is_broadcast_gen=True,
        )
        await state.set_state(GenState.preflight_check)

        locale = _preflight_locale(message.from_user)
        text = compose_preflight_message_html(
            locale,
            prompt_raw="",
            cost=0,
            model=broadcast_model,
            has_photo=True,
            is_edit_mode=False,
            is_broadcast=True,
            use_settings_header=False,
        )
        await message.answer(
            text,
            reply_markup=get_preflight_kb(
                broadcast_model,
                broadcast_ratio,
                "hd" if broadcast_model == "nb2" else "2k",
                locale,
            ),
            parse_mode="HTML",
        )
        return
    # 🔥 КОНЕЦ BROADCAST ЛОГИКИ 🔥
    
    # Обычный флоу (1 или несколько фото)
    if full_caption:
        if await offer_video_if_requested(
            message, state, full_caption,
            photo_file_id=first_photo_file_id,
            clear_state=True,
            locale=locale,
        ):
            return

        if is_lazy_prompt(full_caption):
            await send_lazy_prompt_message(message)
            return

        await start_preflight_check(message, state, full_caption, image_urls)
        return

    await _save_pending_photo(state, image_urls, first_photo_file_id)
    await state.set_state(GenState.waiting_for_caption)
    if count == 1:
        await message.reply(
            t("generation.msg.photo_ready_write_task", locale),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            t("generation.msg.album_received_n_photos_task", locale, count=count),
            parse_mode="HTML",
        )


@router.message(F.text.in_({t("menu.create", "ru"), t("menu.create", "en"), t("menu.create", "es")}))
async def cmd_start_creating(message: types.Message, state: FSMContext):
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    # Явно ставим состояние "свободный режим"
    await state.set_state(GenState.free_mode)
    
    text = t("generation.start_creating_text", locale)
    # Создаем inline-кнопку
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("start.choose_look_button", locale), url="https://t.me/+3ovTRpUPci85ODYy")]
    ])
    
    try:
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
    except TelegramForbiddenError:
        try:
            async with async_session() as session:
                await session.execute(
                    update(User).where(User.telegram_id == message.from_user.id).values(
                        is_blocked=True,
                        blocked_at=datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)
                    )
                )
                await session.commit()
        except Exception:
            pass
        return

    # 👇 ВСТАВИТЬ ЭТО ПОСЛЕ cmd_start_creating 👇

@router.callback_query(F.data == "start_creation_from_guide")
async def cb_start_from_guide(callback: types.CallbackQuery, state: FSMContext):
    """Запуск режима творчества из кнопки Гайда"""
    await callback.answer()
    
    # 1. Включаем режим
    await state.set_state(GenState.free_mode)
    
    # 2. Шлем то же самое сообщение, что и в главном меню
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    text = t("generation.start_from_guide_text", locale)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("start.choose_look_button", locale), url="https://t.me/+3ovTRpUPci85ODYy")]
    ])
    await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)

# 👆 КОНЕЦ ВСТАВКИ 👆
# =====================================================================
# 🔥 COMPLAINT HANDLERS - Кнопки инструкции
# =====================================================================

@router.callback_query(F.data == "retry_correct_flow")
async def cb_retry_correct_flow(callback: types.CallbackQuery, state: FSMContext):
    """
    Запуск правильного сценария генерации после жалобы
    ALWAYS ACTIVE - работает даже через 2 дня
    """
    await callback.answer()
    
    # Сбрасываем любое состояние
    await state.clear()
    
    # Устанавливаем флаг force_pro_mode
    await state.update_data(force_pro_mode=True)
    await state.set_state(GenState.retry_waiting_photos)
    
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    text = t("generation.msg.guide_retry_quality", locale)
    
    await callback.message.answer(text, parse_mode="HTML")
    
    # Логируем
    from app.services.admin_logger import log_retry_flow
    await log_retry_flow(callback.bot, callback.from_user.id)

@router.callback_query(F.data == "complaint_not_now")
async def cb_complaint_not_now(callback: types.CallbackQuery, state: FSMContext):
    """
    Кнопка 'Не сейчас' - возврат в главное меню
    """
    await callback.answer()
    
    # Сбрасываем состояние
    await state.clear()
    
    # Отправляем главное меню
    from app.handlers.start import get_main_kb
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    await callback.message.delete()
    await callback.message.answer(
        t("menu.main", locale),
        reply_markup=get_main_kb(locale)
    )

@router.message(StateFilter(GenState.preflight_check, GenState.selecting_ratio), F.text)
async def handle_new_prompt_during_settings(message: types.Message, state: FSMContext):
    """
    Если юзер был в меню настроек (или выбора формата), 
    но решил просто написать новый промпт — начинаем всё заново.
    """
    # 1. Проверяем, не нажал ли он кнопку меню (Старт, Профиль и т.д.)
    if message.text in IGNORED_TEXTS: 
        return
    
        # 🛡️ ФИЛЬТР КОНТЕНТА (ДОБАВЬ ЭТО)
    if await check_content_filter(message, message.text):
        await state.clear()  # Сбрасываем состояние
        return
    
        # 🚫 ПЕРЕХВАТЧИК ЛЕНИВЫХ ПРОМПТОВ
    if is_lazy_prompt(message.text):
        await state.clear()  # Сбрасываем состояние
        await send_lazy_prompt_message(message)
        return

    data = await state.get_data()
    if await offer_video_if_requested(
        message,
        state,
        message.text,
        photo_file_id=data.get("pending_photo_file_id"),
        clear_state=True,
    ):
        return

    # 2. Сбрасываем старые данные (предыдущий промпт и настройки)
    await state.clear()
    
    # 3. Запускаем новую проверку с новым текстом
    await start_preflight_check(message, state, message.text, None)

# 👆 КОНЕЦ ВСТАВКИ 👆

# Дальше идет твоя старая функция:
# @router.message(F.text, StateFilter(GenState.free_mode, None))
# async def handle_free_text(...):

# 👇 ВСТАВИТЬ ЭТО ПОСЛЕ handle_new_prompt_during_settings 👇

@router.message(StateFilter(GenState.preflight_check, GenState.selecting_ratio), F.photo)
async def handle_new_photo_during_settings(message: types.Message, state: FSMContext, bot: Bot):
    """
    Если юзер был в меню настроек, но прислал ФОТО — сбрасываем и начинаем заново.
    """
    # 1. Если это альбом (несколько фото) — пропускаем, пусть обрабатывает handle_album_input
    # Но для этого нужно добавить состояние в handle_album_input или сбросить его тут.
    # Самый простой способ для альбома — просто сбросить состояние:
    if message.media_group_id:
        await state.clear()
        # Дальше aiogram сам передаст это в handle_album_input, так как состояние уже None
        # Но чтобы сработало наверняка, вызовем его вручную или просто вернемся (т.к. фильтр None сработает)
        return

    # 2. Сбрасываем старые настройки
    await state.clear()
    
    # 3. Обрабатываем фото (копируем логику из handle_general_photo)
    url = await get_photo_url(bot, message.photo[-1].file_id)
    
    if message.caption:
        if await offer_video_if_requested(
            message,
            state,
            message.caption,
            photo_file_id=message.photo[-1].file_id,
            clear_state=True,
        ):
            return
        if is_lazy_prompt(message.caption):
            await send_lazy_prompt_message(message)
            return
        await start_preflight_check(message, state, message.caption, [url])
    else:
        await _save_pending_photo(state, [url], message.photo[-1].file_id)
        await state.set_state(GenState.waiting_for_caption)
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        await message.reply(
            t("generation.msg.photo_accepted_write_prompt", locale),
            parse_mode="HTML",
        )

# 👆 КОНЕЦ ВСТАВКИ 👆

async def send_lazy_prompt_message(message: types.Message):
    """
    Отправляет сообщение-заглушку для ленивых промптов
    Экономит GPU и бананы пользователя
    """
    try:
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        builder = InlineKeyboardBuilder()
        builder.button(
            text=t("start.choose_look_button", locale),
            url="https://t.me/+3ovTRpUPci85ODYy"
        )
        
        text = t("generation.msg.lazy_prompt_body", locale)
        
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
       
           # 📊 Логируем в админский канал
        await log_lazy_prompt_interceptor(
        message.bot,
        message.from_user.id,
        message.from_user.username,
        message.text or message.caption or "Без текста"
    )


    except Exception as e:
        import traceback
        traceback.print_exc()

async def check_content_filter(message: types.Message, text: str) -> bool:
    """
    Проверяет текст через фильтр контента
    
    Returns:
        True - если сообщение заблокировано (нужно остановить обработку)
        False - если всё ок (продолжаем генерацию)
    """
    should_block, matched_word = content_filter.check(text)
    
    # Если NSFW НЕ найдено - пропускаем
    if matched_word is None:
        return False
    
    # Логируем NSFW в админ-чат (FILTER_LOG_CHANNEL_ID)
    await log_nsfw_to_chat(
        bot=message.bot,
        text=text,
        matched_word=matched_word,
        user_id=message.from_user.id,
        username=message.from_user.username or "нет",
        first_name=message.from_user.first_name or "Unknown"
    )
    
    # Если режим активный - блокируем и отправляем сообщение
    if should_block:
        filter_msg = get_filter_message()
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        
        # Создаем кнопку поддержки
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text=t("menu.support", locale), url="https://t.me/nan0banana_help")
        builder.button(text=t("start.choose_look_button", locale), url="https://t.me/+3ovTRpUPci85ODYy")
        builder.adjust(1)
        
        await message.answer(
            filter_msg,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        
        return True  # Блокируем дальнейшую обработку
    
    # Теневой режим - только логируем, не блокируем
    return False

@router.message(GenState.waiting_for_prompt_text, F.text)
async def handle_waiting_for_prompt_text_answer(message: types.Message, state: FSMContext):
    if message.text in IGNORED_TEXTS:
        return
    data = await state.get_data()
    photo_fid = data.get("pending_param_photo_file_id") or data.get("pending_photo_file_id")
    if await offer_video_if_requested(
        message, state, message.text, photo_file_id=photo_fid, clear_state=True
    ):
        return

    main_prompt = data.get("param_main_prompt_template")
    if not main_prompt or not str(main_prompt).strip():
        await state.set_state(GenState.free_mode)
        return
    raw = (message.text or "").strip()
    if not raw:
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        await message.answer(t("prompt.answer_required", locale))
        return
    final_prompt = apply_value_to_main_prompt(main_prompt, raw)
    ratio = data.get("broadcast_ratio", "1:1")
    model = data.get("broadcast_model", "standard")
    cached_fids = list(data.get("pending_param_photo_file_ids") or [])
    cached_fid = data.get("pending_param_photo_file_id")
    if cached_fid and cached_fid not in cached_fids:
        cached_fids.append(cached_fid)

    if cached_fids:
        await state.update_data(
            param_main_prompt_template=None,
            param_question_text=None,
            pending_param_photo_file_id=None,
            pending_param_photo_file_ids=None,
        )
        await enter_broadcast_generation_preflight(
            message, state, message.bot,
            prompt=final_prompt,
            ratio=ratio,
            model=model,
            file_ids=cached_fids,
        )
        return

    await state.update_data(
        broadcast_prompt=final_prompt,
        from_broadcast=True,
        broadcast_ratio=ratio,
        broadcast_model=model,
        param_main_prompt_template=None,
        param_question_text=None,
    )
    await state.set_state(GenState.waiting_for_prompt_photo)
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    await message.answer(
        t("generation.msg.param_send_photo", locale),
        parse_mode="HTML",
    )


async def _cache_photo_while_waiting_for_prompt_text(
    message: types.Message, state: FSMContext, file_id: str
):
    data = await state.get_data()
    q = (data.get("param_question_text") or "").strip()
    if not q:
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        await message.answer(t("prompt.answer_first", locale))
        return
    await state.update_data(
        pending_param_photo_file_id=file_id,
        pending_param_photo_file_ids=[file_id],
    )
    loc = resolve_locale(message.from_user.language_code if message.from_user else None)
    await send_param_prompt_photo_before_text_error(
        message.bot, message.chat.id, q, locale=loc
    )


@router.message(GenState.waiting_for_prompt_text, F.photo)
async def handle_waiting_for_prompt_text_photo(message: types.Message, state: FSMContext):
    if message.media_group_id:
        return
    await _cache_photo_while_waiting_for_prompt_text(
        message, state, message.photo[-1].file_id
    )


@router.message(GenState.waiting_for_prompt_text, F.document)
async def handle_waiting_for_prompt_text_document(message: types.Message, state: FSMContext):
    if not is_image_document(message):
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        await message.answer(t("prompt.text_or_image_required", locale))
        return
    await _cache_photo_while_waiting_for_prompt_text(
        message, state, message.document.file_id
    )


@router.message(GenState.waiting_for_prompt_photo, F.photo)
async def handle_waiting_for_prompt_photo_photo(message: types.Message, state: FSMContext, bot: Bot):
    if message.media_group_id:
        return
    data = await state.get_data()
    if not (data.get("from_broadcast") and data.get("broadcast_prompt")):
        await state.set_state(GenState.free_mode)
        return
    prompt = data.get("broadcast_prompt")
    ratio = data.get("broadcast_ratio", "1:1")
    model = data.get("broadcast_model", "standard")
    await state.update_data(
        from_broadcast=False,
        broadcast_prompt=None,
        broadcast_ratio=None,
    )
    await enter_broadcast_generation_preflight(
        message, state, bot,
        prompt=prompt,
        ratio=ratio,
        model=model,
        file_id=message.photo[-1].file_id,
    )


@router.message(GenState.waiting_for_prompt_photo, F.document)
async def handle_waiting_for_prompt_photo_document(message: types.Message, state: FSMContext, bot: Bot):
    if not is_image_document(message):
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        await message.answer(t("prompt.image_required", locale))
        return
    data = await state.get_data()
    if not (data.get("from_broadcast") and data.get("broadcast_prompt")):
        await state.set_state(GenState.free_mode)
        return
    prompt = data.get("broadcast_prompt")
    ratio = data.get("broadcast_ratio", "1:1")
    model = data.get("broadcast_model", "standard")
    await state.update_data(
        from_broadcast=False,
        broadcast_prompt=None,
        broadcast_ratio=None,
    )
    await enter_broadcast_generation_preflight(
        message, state, bot,
        prompt=prompt,
        ratio=ratio,
        model=model,
        file_id=message.document.file_id,
    )


@router.message(GenState.waiting_for_prompt_photo, F.text)
async def handle_waiting_for_prompt_photo_text(message: types.Message, state: FSMContext):
    if message.text in IGNORED_TEXTS:
        return
    await message.answer(
        t("prompt.send_photo_now", resolve_locale(message.from_user.language_code if message.from_user else None)),
        parse_mode="HTML",
    )


@router.message(
    F.chat.type == "private",
    F.text,
    ~F.text.in_({"🚀 Ускорить Телеграм бесплатно", "🚀 Speed up Telegram for free"}),
    StateFilter(GenState.free_mode, None)
)
async def handle_free_text(message: types.Message, state: FSMContext):
    """Обработка текста без фото"""
    if message.text in IGNORED_TEXTS: 
        return
    
    if await offer_video_if_requested(message, state, message.text, clear_state=True):
        return

        # 🛡️ ФИЛЬТР КОНТЕНТА (добавь ПЕРЕД lazy_prompt)
    if await check_content_filter(message, message.text):
        return
    
        # 🚫 ПЕРЕХВАТЧИК ЛЕНИВЫХ ПРОМПТОВ
    if is_lazy_prompt(message.text):
        await send_lazy_prompt_message(message)
        return
    
    await start_preflight_check(message, state, message.text, None)

@router.message(F.chat.type == "private", F.photo, StateFilter(GenState.free_mode, None))
async def handle_general_photo(message: types.Message, state: FSMContext, bot: Bot):
    """Обработка одиночного фото"""
    if message.media_group_id: 
        return  # Обработается в handle_album_input
    
    print(f"🔥🔥🔥 МОЙ FILE ID: {message.photo[-1].file_id}")
    url = await get_photo_url(bot, message.photo[-1].file_id)
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    
    if not url:
        await message.answer(t("generation.msg.photo_fetch_failed", locale))
        return
    
        # 🔥 ПРОВЕРКА РЕКЛАМНОГО СЦЕНАРИЯ (ПРИОРИТЕТ!)
    data = await state.get_data()
    if data.get('from_ad_scenario') and data.get('ad_scenario_prompt'):
        prompt = data.get('ad_scenario_prompt')
        ratio = data.get('ad_scenario_ratio', '1:1')
        model = data.get('ad_scenario_model', 'standard')
        
        # Сразу запускаем preflight с настройками сценария
        await start_preflight_check(message, state, prompt, [url])
        return
    
    # 🔥 ПРОВЕРКА BROADCAST ПРОМПТА 🔥
    data = await state.get_data()
    force_pro_mode = data.get('force_pro_mode', False)  # 👈 СОХРАНЯЕМ
    if data.get('from_broadcast') and data.get('broadcast_prompt'):
        prompt = data.get('broadcast_prompt')
        ratio = data.get('broadcast_ratio', '1:1')
        model = data.get('broadcast_model', 'standard')  # 👈 ДОБАВЬ ЭТУ СТРОКУ
        
        
        # Очищаем флаги
        await state.update_data(from_broadcast=False, broadcast_prompt=None, broadcast_ratio=None)
        
        # Сохраняем данные для preflight
        await state.update_data(
            pf_prompt=prompt,
            pf_image_urls=[url],
            pf_ratio=ratio,
            pf_model=model,  # 👈 ИСПОЛЬЗУЕМ МОДЕЛЬ ИЗ ПОСТА
            is_broadcast_gen=True  # 👈 ДОБАВЬ ФЛАГ
)
        await state.set_state(GenState.preflight_check)

        locale = _preflight_locale(message.from_user)
        text = compose_preflight_message_html(
            locale,
            prompt_raw="",
            cost=0,
            model=model,
            has_photo=True,
            is_edit_mode=False,
            is_broadcast=True,
            use_settings_header=False,
        )
        await message.answer(
            text,
            reply_markup=get_preflight_kb(model, ratio, "1k", locale),
            parse_mode="HTML",
        )
        return
    
    if message.caption:
        if await offer_video_if_requested(
            message,
            state,
            message.caption,
            photo_file_id=message.photo[-1].file_id,
            clear_state=True,
            locale=locale,
        ):
            return

        # 🚫 ПЕРЕХВАТЧИК ЛЕНИВЫХ ПРОМПТОВ
        lazy_check = is_lazy_prompt(message.caption)  
        if lazy_check:
            await send_lazy_prompt_message(message)
            return
        
        # 🔥 ВОТ ЧТО БЫЛО ПОТЕРЯНО - ЗАПУСК ГЕНЕРАЦИИ!
        await start_preflight_check(message, state, message.caption, [url])
        return  # ← ВАЖНО: выходим после запуска
    
    else:
        # 🔥 ВОССТАНАВЛИВАЕМ force_pro_mode ЕСЛИ БЫЛ 👇
        if force_pro_mode:
            await state.update_data(force_pro_mode=True)

        await _save_pending_photo(state, [url], message.photo[-1].file_id)
        await state.set_state(GenState.waiting_for_caption)

        await message.reply(
            t("generation.msg.photo_accepted_write_prompt", locale),
            parse_mode="HTML",
        )

@router.message(GenState.retry_waiting_photos, F.photo)
async def handle_retry_photos(message: types.Message, state: FSMContext, bot: Bot):
    """
    Обработка фото в режиме ретрая (после жалобы)
    """
    if message.media_group_id:
        return  # Обработается в handle_album_input
    
    url = await get_photo_url(bot, message.photo[-1].file_id)
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)

    if message.caption:
        if await offer_video_if_requested(
            message,
            state,
            message.caption,
            photo_file_id=message.photo[-1].file_id,
            clear_state=True,
            locale=locale,
        ):
            return
        await state.update_data(pf_image_urls=[url])
        await start_preflight_check(message, state, message.caption, [url])
    else:
        await _save_pending_photo(state, [url], message.photo[-1].file_id)
        await message.reply(
            t("generation.msg.photo_accepted_face_rules", locale),
            parse_mode="HTML"
        )

@router.message(GenState.retry_waiting_photos, F.text)
async def handle_retry_text_prompt(message: types.Message, state: FSMContext):
    """
    Обработка текстового промпта после фото в режиме ретрая
    """

        # 🛡️ ФИЛЬТР КОНТЕНТА (ДОБАВЬ ЭТО)
    if await check_content_filter(message, message.text):
        return
    
    data = await state.get_data()
    image_urls = data.get("pending_image_urls")

    if not image_urls:
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        await message.answer(t("generation.msg.need_photo_first", locale))
        return

    if await offer_video_if_requested(
        message,
        state,
        message.text,
        photo_file_id=data.get("pending_photo_file_id"),
        clear_state=True,
    ):
        return

    await start_preflight_check(message, state, message.text, image_urls)

@router.message(GenState.waiting_for_caption, F.text)
async def handle_delayed_caption(message: types.Message, state: FSMContext):
    """Обработка отложенного текста после фото"""
    user_prompt = message.text

        # 🛡️ ФИЛЬТР КОНТЕНТА (добавь ПЕРЕД lazy_prompt)
    if await check_content_filter(message, message.text):
        return
    
    data = await state.get_data()
    if await offer_video_if_requested(
        message,
        state,
        user_prompt,
        photo_file_id=data.get("pending_photo_file_id"),
        clear_state=True,
    ):
        return

        # 🚫 ПЕРЕХВАТЧИК ЛЕНИВЫХ ПРОМПТОВ 👇
    if is_lazy_prompt(user_prompt):
        await send_lazy_prompt_message(message)
        return
    # 👆 КОНЕЦ ВСТАВКИ
        # 🎨 Проверяем blend-задачу
    if is_blend_request(user_prompt):
        await state.update_data(is_blend_mode=True)
    data = await state.get_data()
    image_urls = data.get("pending_image_urls")
    
    if not image_urls:
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        await message.answer(t("generation.msg.photos_missing_error", locale))
        await state.clear()
        return
    
    await start_preflight_check(message, state, user_prompt, image_urls)

# =====================================================================
# ОБРАБОТКА РЕЗУЛЬТАТОВ
# =====================================================================
@router.callback_query(F.data.startswith("reroll_"))
async def cb_reroll(callback: types.CallbackQuery, bot: Bot):
    """Перегенерация с теми же параметрами"""
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    await callback.answer(t("generation.alert.reroll_starting", locale), show_alert=False)
    
    try:
        db_id = int(callback.data.split("_")[1])
        
        async with async_session() as session:
            history_item = await get_history_message_by_id(session, db_id)
        
        if not history_item or not history_item.content:
            await callback.message.answer(t("generation.msg.reroll_stale", locale))
            return
        
        params = json.loads(history_item.content)
        
        await callback.message.reply(
            t("generation.msg.reroll_generating", locale),
            parse_mode="HTML",
        )
        
        await process_generation(
            callback.message,
            callback.from_user.id,
            params.get("prompt"),
            params.get("image_urls"),  # ✅ Уже список
            params.get("ratio", "1:1"),
            params.get("cost", 1),
            params.get("pro", False),
            params.get("nb2", False),
            params.get("resolution", "1K"),
            is_blend_mode=params.get("is_blend_mode", False),
            locale=locale,
        )
    except Exception as e:
        print(f"❌ Ошибка reroll: {e}")
        await callback.answer(t("generation.alert.reroll_failed", locale), show_alert=True)

@router.callback_query(F.data.startswith("download_video_"))
async def cb_download_video(callback: types.CallbackQuery, bot: Bot):
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    await callback.answer(t("generation.alert.downloading", locale))
    
    task_id = callback.data.replace("download_video_", "")
    
    async with async_session() as session:
        from app.services.video_service import get_task_by_id
        task = await get_task_by_id(session, task_id)
        
        if not task or not task.result_video_url:
            await callback.answer(t("generation.alert.video_not_found", locale), show_alert=True)
            return
        
        # Скачиваем и отправляем как document
        try:
            timeout = aiohttp.ClientTimeout(total=300)
            connector = aiohttp.TCPConnector(ssl=False)
            
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as http_session:
                async with http_session.get(task.result_video_url) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")
                    
                    video_bytes = await resp.read()
            
            # Отправляем как document (без сжатия)
            video_file = types.BufferedInputFile(video_bytes, filename="video_original.mp4")
            
            await bot.send_document(
                chat_id=callback.from_user.id,
                document=video_file,
                caption=t("generation.caption.original_uncompressed", locale),
                disable_content_type_detection=True,  # ← Добавь эту строку
                request_timeout=300
            )
            
        except Exception as e:
            print(f"Ошибка скачивания видео: {e}")
            await bot.send_message(
                callback.from_user.id,
                t("generation.msg.download_fallback_link", locale, url=task.result_video_url)
            )

@router.callback_query(F.data.startswith("download_"))
async def cb_download(callback: types.CallbackQuery, bot: Bot):
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    await callback.answer(t("generation.alert.downloading", locale))
    
    try:
        db_id = int(callback.data.split("_")[1])
        async with async_session() as session: 
            history_item = await get_history_message_by_id(session, db_id)
        
        if not history_item:
            await callback.answer(t("generation.alert.record_not_found", locale), show_alert=True)
            return

        if history_item.image_url:
            try:
                # Таймаут 30 секунд
                timeout = aiohttp.ClientTimeout(total=30)
                
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    # ssl=False для совместимости с провайдером
                    async with session.get(history_item.image_url, ssl=False) as resp:
                        if resp.status == 200:
                            # Читаем файл
                            data = await resp.read()
                            
                            # Проверка на пустой файл
                            if len(data) == 0:
                                raise Exception("Пустой файл")

                            input_file = types.BufferedInputFile(data, filename=f"image_{db_id}.png")
                            
                            await bot.send_document(
                                chat_id=callback.from_user.id, 
                                document=input_file, 
                                caption=t("generation.caption.original_quality", locale)
                            )
                        else:
                            await callback.answer(t("generation.alert.img_server_error", locale, status=resp.status), show_alert=True)
            except Exception as e:
                print(f"Ошибка скачивания: {e}")
                # Fallback: отправляем ссылку
                try:
                    await bot.send_message(
                        chat_id=callback.from_user.id,
                        text=t("generation.msg.download_fallback_link", locale, url=history_item.image_url)
                    )
                except:
                    await callback.answer(t("generation.alert.file_fetch_failed", locale), show_alert=True)

        elif history_item.file_id:
            await bot.send_photo(
                chat_id=callback.from_user.id, 
                photo=history_item.file_id, 
                caption=t("generation.caption.telegram_fallback", locale)
            )
        else: 
            await callback.answer(t("generation.alert.file_lost", locale), show_alert=True)

    except Exception as e:
        print(f"❌ Ошибка download: {e}")
        await callback.answer(t("generation.alert.upload_error", locale), show_alert=True)


@router.callback_query(F.data.startswith("edit_"))
async def cb_edit_result(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """Редактирование существующего результата"""
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    await callback.answer()
    
    try:
        db_id = int(callback.data.split("_")[1])
        
        async with async_session() as session: 
            history_item = await get_history_message_by_id(session, db_id)
        
        if not history_item or not history_item.file_id:
            await callback.answer(t("generation.alert.source_not_found", locale), show_alert=True)
            return
        
        # Определяем стоимость из истории
        try: 
            params = json.loads(history_item.content)
            use_pro = params.get("pro", False)
            use_nb2 = params.get("nb2", False)
            ratio = params.get("ratio", "1:1")
        except: 
            use_pro = False
            use_nb2 = False
            ratio = "1:1"
        
        # Определяем тип модели для расчета стоимости
        if use_pro:
            model_type = "pro"
        elif use_nb2:
            model_type = "nb2"
        else:
            model_type = "standard"
        
        cost = calc_cost(model_type, "hd")
        
        original_ratio = ratio
        
        await state.update_data(
            editing_file_id=history_item.file_id,
            edit_use_pro=use_pro,
            edit_cost=cost,
            editing_original_ratio=original_ratio,  # ← сохраняем ratio оригинала
        )
        await state.set_state(GenState.waiting_for_edit_instruction)
        
        await callback.message.reply(
            t("generation.msg.edit_mode_prompt", locale, cost=cost),
            reply_markup=get_cancel_kb(locale),
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"❌ Ошибка edit: {e}")
        await callback.answer(t("generation.alert.edit_failed", locale), show_alert=True)

@router.message(GenState.waiting_for_edit_instruction, F.text)
async def handle_edit_instruction(message: types.Message, state: FSMContext, bot: Bot):
    """Обработка инструкции для редактирования"""
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    instruction = message.text
    data = await state.get_data()
    file_id = data.get("editing_file_id")

    if not file_id:
        await message.answer(t("generation.msg.source_photo_not_found", locale))
        await state.clear()
        return

    if await offer_video_if_requested(
        message, state, instruction, photo_file_id=file_id, clear_state=True, locale=locale
    ):
        return

    img_url = await get_photo_url(bot, file_id)
    
    if not img_url:
        await message.answer(t("generation.msg.photo_fetch_failed", locale))
        await state.clear()
        return
    
    data = await state.get_data()
    original_ratio = data.get("editing_original_ratio", None)
    await start_preflight_check(message, state, instruction, [img_url], is_edit_mode=True, initial_ratio=original_ratio)

# =====================================================================
# КОМАНДЫ
# =====================================================================
@router.message(Command("clear"))
async def cmd_clear_history(message: types.Message, state: FSMContext):
    """Очистка истории"""
    async with async_session() as session: 
        await clear_history(session, message.from_user.id)
    await state.clear()
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    await message.answer(t("generation.msg.memory_cleared", locale), parse_mode="HTML")


@router.callback_query(F.data == "cancel_wizard")
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Отмена мастера"""
    await state.clear()
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    await callback.message.edit_text(t("generation.msg.action_cancelled", locale))
    await callback.answer()

@router.callback_query(F.data.startswith("cat_"))
async def cb_select_category(callback: types.CallbackQuery, state: FSMContext):
    """Выбор категории генерации"""
    await callback.answer()
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    
    category = callback.data.split("_")[1]
    await state.clear()
    await state.update_data(selected_category=category)
    
    if category == "pro":
        await state.set_state(GenState.free_mode)
        await callback.message.edit_text(
            t("generation.msg.category_pro_mode", locale),
            parse_mode="HTML",
        )
        return
    
    if category == "replace":
        await state.set_state(GenState.waiting_for_base_image)
        await callback.message.edit_text(
            t("generation.msg.category_replace_step1", locale),
            reply_markup=get_cancel_kb(locale),
            parse_mode="HTML",
        )
        return
    
    if category == "free":
        await state.set_state(GenState.free_mode)
        await callback.message.edit_text(
            t("generation.msg.category_free_mode", locale),
            parse_mode="HTML",
        )
    else:
        await state.set_state(GenState.waiting_for_category_input)
        await callback.message.edit_text(
            t("generation.msg.category_selected", locale),
            parse_mode="HTML",
        )

KOREAN_TREND_STOP_WORDS = [
    "spotv",
    "kbo",
    "야구장",
    "관중석",
    "korean professional baseball",  # из нового шаблона
    "korean baseball",
    "입고 싶은 옷 입력",  # плейсхолдер из копипасты
    "@meaningless:",  # метка автора оригинального промпта
    "broadcast screenshot-style",
    "корейского профессионального бейсбольного",
    "scre-enshot",  # опечатка переводчика
    "sea ts",  # опечатка переводчика
    "i mage",  # опечатка переводчика
    "входной сигнал]",  # кривой перевод инпута одежды
    "широковещательный кадр",  # машинный перевод
]

_INVISIBLE_FOR_TREND = frozenset(
    "\u200b\u200c\u200d\ufeff\u2060\u180e\u00ad"
)


def _strip_invisible_trend_chars(s: str) -> str:
    return "".join(c for c in s if c not in _INVISIBLE_FOR_TREND)


def _normalize_prompt_for_korean_trend(prompt: str | None) -> str:
    s = _strip_invisible_trend_chars(prompt or "")
    return unicodedata.normalize("NFKC", s).casefold()


def _find_korean_trend_stop_word(normalized: str) -> str | None:
    for phrase in KOREAN_TREND_STOP_WORDS:
        needle = unicodedata.normalize("NFKC", phrase).casefold()
        if needle in normalized:
            return phrase
    return None


async def _korean_trend_generation_allowed(
    bot: Bot,
    message: types.Message,
    user_id: int,
    prompt: str | None,
    locale: str | None,
) -> bool:
    """
    Пейволл по стоп-словам KBO-тренда. Без успешных оплат — обнуляем free-баланс и блокируем.
    Returns True если генерацию можно продолжать, False если нужно прервать.
    """
    matched = _find_korean_trend_stop_word(_normalize_prompt_for_korean_trend(prompt))
    if matched is None:
        return True

    async with async_session() as session:
        resolved_locale = await effective_locale(bot, message, user_id, locale, session=session)
        if await has_user_purchased(session, user_id):
            return True

        result = await session.execute(
            select(User).where(User.telegram_id == user_id).with_for_update()
        )
        user = result.scalar_one_or_none()
        if user:
            user.balance_free = 0
            user.generations_balance = user.balance_paid + user.balance_free
        await session.commit()

    uname = message.from_user.username if message.from_user else None
    await log_content_filter(
        bot=bot,
        user_id=user_id,
        username=uname or "",
        text=prompt or "",
        trigger_type="korean_trend_block",
        matched_word=matched,
        was_blocked=True,
        channel_id=getattr(config, "ADMIN_CHANNEL_ID", None),
    )

    paywall_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🍌 Купить бананы", callback_data="buy_menu")]
        ]
    )
    await message.answer(
        t("generation.korean_trend_paywall", resolved_locale),
        reply_markup=paywall_kb,
        parse_mode="HTML",
    )
    return False


# ==============================================================================
# 🔥 ГЛАВНАЯ ФУНКЦИЯ ГЕНЕРАЦИИ
# ==============================================================================
async def process_generation(
    message: types.Message,
    user_id: int,
    prompt: str,
    image_urls,  # list или None
    aspect_ratio: str = "1:1",
    cost: int = 1,
    use_pro_model: bool = False,
    use_nb2_model: bool = False,
    resolution: str = "1K",
    is_blend_mode: bool = False,
    post_id: str = None,
    locale: str | None = None,
):
    """
    Оркестратор генерации изображений.
    Координирует все этапы: проверка -> подготовка -> генерация -> сохранение
    """
    from app.services.generation import (
        build_collage_if_needed,
        execute_ai_generation,
        save_generation_result,
        award_referral_bonus,
    )
    
    bot = message.bot
    final_urls = normalize_image_urls(image_urls)

    # =====================================================================
    # ШАГ 1: ПРЕДВАРИТЕЛЬНЫЕ ПРОВЕРКИ
    # =====================================================================
    
    if not await _korean_trend_generation_allowed(bot, message, user_id, prompt, locale):
        return

    # Проверка и списание баланса
    async with async_session() as session:
        locale = await effective_locale(bot, message, user_id, locale, session=session)
        model_type = "pro" if use_pro_model else "nb2" if use_nb2_model else "standard"
        kie_credits = get_kie_credits(model_type, resolution)
        user = await get_user(session, user_id)
        image_hashes: list[str] = []

        # Проверка дубликатов фото
        if final_urls:
            has_purchases = await has_user_purchased(session, user_id)
            if should_run_image_hash_check(final_urls, user, has_purchases=has_purchases):
                try:
                    image_hashes = await compute_phashes_for_urls(final_urls)
                except ImageHashError as exc:
                    logger.warning("Skipping duplicate-photo guard for user %s: %s", user_id, exc)
                else:
                    duplicate_hash = await find_recent_duplicate_hash(
                        session,
                        hash_values=image_hashes,
                        user_id=user_id,
                    )
                    if duplicate_hash is not None and user is not None:
                        balance_before = user.generations_balance
                        await log_duplicate_photo_interceptor(
                            bot=bot,
                            user_id=user_id,
                            username=message.chat.username,
                            duplicate_owner_id=duplicate_hash.user_id,
                            image_hash=duplicate_hash.hash,
                            prompt=prompt,
                            balance_before=balance_before,
                        )
                        apply_duplicate_penalty(user)
                        await session.commit()
                        await message.answer(
                            t("generation.abuse.duplicate_photo_message", locale),
                            reply_markup=get_duplicate_photo_block_kb(locale),
                        )
                        return
                    if image_hashes:
                        await store_image_hashes(
                            session,
                            hash_values=image_hashes,
                            user_id=user_id,
                        )

        # Списание баланса
        has_balance, transaction_id = await check_and_deduct_balance(
            session, user_id, amount=cost, post_id=post_id,
            model_type=model_type, kie_credits_cost=kie_credits
        )
        balance_left = await get_user_balance(session, user_id)

        if not has_balance:
            alert_text, alert_kb = await get_smart_alert_message(
                session, user_id, balance_left, cost, locale
            )
            await message.answer(
                alert_text,
                reply_markup=alert_kb.as_markup(),
                parse_mode="HTML"
            )
            return

    # =====================================================================
    # ШАГ 2: СООБЩЕНИЕ О СТАРТЕ
    # =====================================================================
    
    is_complex_standard = (
        not use_pro_model and not use_nb2_model and len(final_urls) >= 2
    )
    
    if is_complex_standard:
        wait_msg = await message.answer(
            t("generation.msg.creating_complex_standard", locale),
            parse_mode="HTML",
        )
        should_delete_wait_msg = False
    else:
        wait_msg = await message.answer(
            t("generation.msg.creating_simple", locale),
            parse_mode="HTML",
        )
        should_delete_wait_msg = True

    # =====================================================================
    # ШАГ 3: ГЕНЕРАЦИЯ (обёрнута в try/except)
    # =====================================================================
    
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)
        
        # Подготовка изображений (коллаж если нужно)
        final_urls, prompt = await build_collage_if_needed(
            bot=bot,
            user_id=user_id,
            final_urls=final_urls,
            prompt=prompt,
            use_pro_model=use_pro_model,
            use_nb2_model=use_nb2_model,
            is_blend_mode=is_blend_mode,
        )
        
        # Вызов AI API
        result_file, source_url, kie_task_id = await execute_ai_generation(
            bot=bot,
            prompt=prompt,
            final_urls=final_urls,
            aspect_ratio=aspect_ratio,
            use_pro_model=use_pro_model,
            use_nb2_model=use_nb2_model,
            resolution=resolution,
            transaction_id=transaction_id,
        )
        
        # =====================================================================
        # ШАГ 4: ОБРАБОТКА РЕЗУЛЬТАТА
        # =====================================================================
        
        if result_file:
            # Удаляем сообщение ожидания (если нужно)
            if should_delete_wait_msg:
                try:
                    await wait_msg.delete()
                except:
                    pass
            
            # Отправка и сохранение результата
            db_id, sent_file_id = await save_generation_result(
                bot=bot,
                message=message,
                user_id=user_id,
                prompt=prompt,
                final_urls=final_urls,
                result_file=result_file,
                source_url=source_url,
                balance_left=balance_left,
                cost=cost,
                use_pro_model=use_pro_model,
                use_nb2_model=use_nb2_model,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                is_blend_mode=is_blend_mode,
                locale=locale,
            )
            
            # Начисление реферального бонуса (если первая генерация)
            await award_referral_bonus(
                bot=bot,
                user_id=user_id,
                locale=locale,
            )
        else:
            # ❌ NULL ОТВЕТ - ВОЗВРАТ ДЕНЕГ
            await handle_null_result_error(
                bot=bot,
                user_id=user_id,
                username=message.chat.username,
                prompt=prompt,
                cost=cost,
                locale=locale,
                wait_message=wait_msg,
                reply_message=message,
            )
                
    except Exception as e:
        # Универсальная обработка ошибок генерации
        await handle_generation_error(
            bot=bot,
            user_id=user_id,
            username=message.chat.username,
            prompt=prompt,
            cost=cost,
            error=e,
            locale=locale,
            wait_message=wait_msg,
            reply_message=message,
        )
@router.callback_query(F.data.regexp(r"^bc_\d+$"))  # только bc_123, не bc_model_
async def cb_broadcast_generate(callback: types.CallbackQuery, state: FSMContext):
    """Обработка нажатия кнопки генерации из рассылки"""
    
    broadcast_id = int(callback.data.split("_")[1])
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    
    async with async_session() as session:
        result = await session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )
        broadcast = result.scalar_one_or_none()

        if not broadcast:
            await callback.answer(t("generation.alert.broadcast_not_found", locale), show_alert=True)
            return

        pq = (broadcast.param_question or "").strip()
        has_param_question = bool(pq)

        if not broadcast.hidden_prompt:
            await callback.answer(t("generation.alert.broadcast_prompt_missing", locale), show_alert=True)
            return

        broadcast.clicks_count += 1
        await session.commit()

    if has_param_question:
        pq_clean = pq
        await state.update_data(
            param_main_prompt_template=broadcast.hidden_prompt,
            param_question_text=pq_clean,
            broadcast_ratio=broadcast.aspect_ratio or "1:1",
            broadcast_model=broadcast.model_type or "standard",
            from_broadcast=True,
            pending_param_photo_file_id=None,
            no_standard_model=True,  # ← добавить
        )
        await state.set_state(GenState.waiting_for_prompt_text)
        await send_param_prompt_text_intro(
            callback.bot,
            callback.from_user.id,
            pq_clean,
            locale=resolve_locale(callback.from_user.language_code if callback.from_user else None),
        )
        await callback.answer()
        return

    await state.update_data(
        broadcast_prompt=broadcast.hidden_prompt,
        broadcast_ratio=broadcast.aspect_ratio or "1:1",
        broadcast_model=broadcast.model_type or "standard",
        from_broadcast=True,
        no_standard_model=True,  # ← добавить
    )
    await state.set_state(GenState.free_mode)

    await callback.message.answer(
        t("generation.msg.broadcast_send_photo_applied", locale),
        parse_mode="HTML",
    )

    await callback.answer()

    # =====================================================================
# 🎬 VIDEO GENERATION HANDLERS (перенесено в generation_flow/video.py)
# =====================================================================

@router.message(F.text, StateFilter(GenState.free_mode))
async def handle_text_in_free_mode(message: types.Message, state: FSMContext):
    """Обработка текста когда ожидаем фото"""
    
    data = await state.get_data()
    
    # 🔥 ЕСЛИ ЭТО ПОЛЬЗОВАТЕЛЬ ИЗ РЕКЛАМЫ
    if data.get('from_ad_scenario'):
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("generation.ad.cancel_generation_button", locale), callback_data="cancel_generation")]
        ])
        
        await message.answer(
            t("generation.msg.ad_scenario_need_photo", locale),
            parse_mode="HTML",
            reply_markup=keyboard
        )
        return

@router.callback_query(F.data == "cancel_generation")
async def callback_cancel_generation(callback: CallbackQuery, state: FSMContext):
    """Отмена генерации и возврат в главное меню"""
    await state.clear()
    locale = resolve_locale(callback.from_user.language_code if callback.from_user else None)
    
    await callback.message.edit_text(
        t("generation.msg.cancel_use_buttons_below", locale),
        parse_mode="HTML"
    )
    
    await callback.answer()
