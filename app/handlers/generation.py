import json
import io
from PIL import Image
from aiogram import Router, types, F, Bot
from aiogram.filters import StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatAction
from aiogram import html
import aiohttp
from app.services.admin_logger import log_generation, log_error, log_lazy_prompt_interceptor, log_referral, log_security_ban,log_content_filter
from app.models import User, Broadcast, PostConfig, BananaTransaction
from app.middlewares.content_filter import ContentFilter, FilterMode, get_filter_message, log_nsfw_to_chat
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from app.database import async_session
from app.services.user_service import (
    check_and_deduct_balance, get_user_balance, is_user_premium, 
    add_history, clear_history, get_history_message_by_id, get_dialog_context,
    start_generation_task, finish_generation_task, admin_change_balance,
    get_user_model_preference, set_user_model_preference, has_user_purchased, get_user, track_banana_transaction, increment_generations_count
)
from app.services.ai_engine import generate_image
from app.services.kie_pricing import get_kie_credits
from app.utils import prompts
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, update, select
from app.utils.prompt_validator import is_lazy_prompt
from app import config
import asyncio
import logging
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
logger = logging.getLogger(__name__)
content_filter = ContentFilter(
    FilterMode[config.FILTER_MODE.upper()]  # "shadow" -> FilterMode.SHADOW
)

def calc_cost(model_type: str, quality: str) -> int:
    if model_type == "pro":
        if quality == "4k": return config.COST_PRO_4K
        elif quality == "2k": return config.COST_PRO_2K
        else: return config.COST_PRO_1K
    elif model_type == "nb2":
        if quality == "4k": return config.COST_NB2_4K
        elif quality == "2k": return config.COST_NB2_2K
        else: return config.COST_NB2_1K
    return config.COST_STANDARD

router = Router()

COMPLAINT_INSTRUCTION_PHOTO = "AgACAgIAAxkBAALT5Wljc3V_Fhya4RZZ0xab7eXhFtE-AAIZDGsbxiYgS76CBLyQRXTjAQADAgADeQADOAQ"  # 👈 Вставь свой file_id


# 👇 ЗАМЕНИТЬ ВЕСЬ СПИСОК IGNORED_TEXTS НА ЭТОТ:
IGNORED_TEXTS = [
    "✨ Начать творить", "🎨 Создать изображение", "Заработать🍌", "📚 Гайд",
    "📸 Примеры работ", "👤 Профиль", "👤 Мой профиль", "💬 Поддержка",
    "🍌 Купить бананы", "Фарминг🍌", "ℹ️ О нас", "ℹ️ Что умеет бот?",
    "/start", "/help", "/admin", "/stats", "/clear", "/admin_scenarios", "🚀 Ускорить Телеграм бесплатно",
    # 👇 КОМАНДЫ БОКОВОГО МЕНЮ
    "/start", "/help", "/admin", "/stats", "/clear",
    "/profile", "/free", "/about", "/support", "/guide", "/proxy"
]

PARAM_USER_VALUE_MAX_LEN = 500


def apply_value_to_main_prompt(main_prompt: str, user_value: str) -> str:
    v = (user_value or "").strip()
    if len(v) > PARAM_USER_VALUE_MAX_LEN:
        v = v[:PARAM_USER_VALUE_MAX_LEN]
    return (main_prompt or "").replace("{value}", v)


async def send_param_prompt_text_intro(
    bot: Bot,
    chat_id: int,
    question: str,
    reply_markup: types.ReplyKeyboardMarkup | types.ReplyKeyboardRemove | None = None,
) -> None:
    await bot.send_message(
        chat_id,
        "<b>🔥 Отлично! Промпт уже применен.</b>\n\n"
        "Чтобы образ получился идеальным, нужно уточнить:",
        parse_mode="HTML",
    )
    await asyncio.sleep(0.8)
    safe_q = html.quote(question)
    await bot.send_message(
        chat_id,
        f"❓ <i>{safe_q}</i>\n\n"
        "<b>Напишите ваш ответ прямо в этот чат👇</b>",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def send_param_prompt_photo_before_text_error(bot: Bot, chat_id: int, question: str) -> None:
    await bot.send_message(
        chat_id,
        "📸 <b>Фото вижу, но мы пропустили один шаг!</b>\n\n"
        "Чтобы образ получился идеальным, сначала ответьте на вопрос:",
        parse_mode="HTML",
    )
    await asyncio.sleep(0.8)
    safe_q = html.quote(question)
    await bot.send_message(
        chat_id,
        f"❓ <i>{safe_q}</i>\n\n"
        "<b>Просто напишите ответ в этот чат 👇</b>",
        parse_mode="HTML",
    )


def _is_image_document(message: types.Message) -> bool:
    d = message.document
    if not d:
        return False
    mt = (d.mime_type or "").lower()
    if mt.startswith("image/"):
        return True
    fn = (d.file_name or "").lower()
    return any(fn.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"))


async def enter_broadcast_generation_preflight(
    message: types.Message,
    state: FSMContext,
    bot: Bot,
    *,
    prompt: str,
    ratio: str,
    model: str,
    file_id: str,
) -> None:
    """Собраны промпт + фото (рассылка/post link) → preflight как при обычной отправке фото."""
    url = await get_photo_url(bot, file_id)
    if not url:
        await message.answer("❌ Не удалось получить фото.")
        return
    await state.update_data(
        from_broadcast=False,
        broadcast_prompt=None,
        broadcast_ratio=None,
    )
    await state.update_data(
        pf_prompt=prompt,
        pf_image_urls=[url],
        pf_ratio=ratio,
        pf_model=model,
        is_broadcast_gen=True,
    )
    await state.set_state(GenState.preflight_check)
    text = (
        "🎨 *Параметры генерации*\n\n"
        "Выбери модель и жми \"🚀 Запуск\"👇"
    )
    await message.answer(
        text,
        reply_markup=get_preflight_kb(model, ratio, "1k"),
        parse_mode="Markdown",
    )


async def get_smart_alert_message(session, user_id: int, balance: int, cost: int) -> tuple[str, InlineKeyboardBuilder]:
    """
    Возвращает умное сообщение и клавиатуру в зависимости от сценария
    
    Returns:
        (text, keyboard_builder)
    """
    has_purchases = await has_user_purchased(session, user_id)
    
    builder = InlineKeyboardBuilder()
    
    # 🔹 СЦЕНАРИЙ В: Не хватает чуть-чуть (Баланс > 0, но < Цены)
    if balance > 0 and balance < cost:
        text = (
            "🎨 <b>Маловато для шедевра!</b>\n\n"
            f"Для этого действия нужно <b>{cost} 🍌</b>, а у тебя осталось всего <b>{balance} 🍌</b>.\n\n"
            "👇 Докупи бананов, чтобы продолжить:"
        )
        builder.button(text="💰 Купить бананы", callback_data="goto_shop")
        builder.adjust(1)
        return text, builder
    
    # 🔹 СЦЕНАРИЙ Б: Опытный (Баланс 0, покупки были)
    if balance == 0 and has_purchases:
        text = (
            "🙈 <b>Ой, бананы закончились!</b>\n\n"
            "На балансе 0 бананов. Не останавливайся на достигнутом — пополни баланс и твори дальше!\n\n"
            "👇 Пополни баланс:"
        )
        builder.button(text="💰 Купить бананы", callback_data="goto_shop")
        builder.adjust(1)
        return text, builder
    
    # 🔹 СЦЕНАРИЙ А: Новичок (Баланс 0, покупок не было)
    text = (
        "🙈 <b>Ой, бананы закончились!</b>\n\n"
        "Ты так увлекся творчеством, что запасы иссякли. Но не беда!\n\n"
        "👇 Пополни запас прямо сейчас (от 4.9₽/шт):"
    )
    builder.button(text="💰 Купить бананы", callback_data="goto_shop")
    builder.button(text="Заработать🍌", callback_data="goto_free")
    builder.adjust(1)
    return text, builder

# =====================================================================
# 🔥 COMPLAINT FILTER - Обработка жалоб на сходство
# =====================================================================

async def send_complaint_instruction(message: types.Message):
    """
    Отправляет инструкцию при срабатывании фильтра жалоб
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Повторить правильно", callback_data="retry_correct_flow")
    builder.button(text="❌ Не сейчас", callback_data="complaint_not_now")
    builder.adjust(1)
    
    # Текст с правилами в блоке кода
    instruction_text = (
        "🎯 <b>Как получить хороший результат и 100% сходство:</b>\n\n"
        "Чтобы нейросеть создала вашего двойника, нужно выполнить <b>все 3 условия:</b>\n\n"
        "1️⃣ <b>Фото:</b> это 80% успеха. Нейросеть рисует то, что видит. Пришлите четкое фото анфас(прямо в камеру). Важно: хороший дневной свет, без теней, без очков, лицо не закрыто волосами или руками.\n\n"
        "2️⃣ <b>Режим:</b> Включите <b>PRO модель</b>. Только она переносит черты лица с фотографической точностью. Standard — для артов, общих образов и не сохраняет лицо.\n\n"
        "3️⃣ <b>Промпт:</b> Добавьте к вашему описанию этот текст (нажмите, чтобы скопировать):\n"
        "<code>Сохрани идентичные черты: форма лица, глаза, нос, губы, тон кожи и возраст. Без улучшения внешности, без бьюти-фильтров, без морфинга лица.</code>\n\n"
        "⚠️ <b>Важно:</b> Результат гарантирован только при соблюдении всех трёх пунктов одновременно!\n\n"
        "<b>Повторить правильно? </b>👇"
    )
    
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
        message.text
    )

class GenState(StatesGroup):
    waiting_for_category_input = State() 
    waiting_for_caption = State()
    waiting_for_base_image = State()
    waiting_for_ref_image = State()
    waiting_for_replace_object_text = State()
    free_mode = State()
    waiting_for_ratio = State()
    preflight_check = State()
    selecting_ratio = State()
    waiting_for_edit_instruction = State()
    retry_waiting_photos = State()  # 👈 Новое состояние для ретрая
    waiting_for_video_source = State()  # Ожидание фото для генерации видео
    waiting_for_prompt_text = State()  # Текстовый ответ на вопрос промпта (рассылка / post link)
    waiting_for_prompt_photo = State()  # Фото после ответа на вопрос


# =====================================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================
def smart_compress_image(file_bytes: bytes) -> bytes:
    """Сжимает изображение если > 9.5 МБ"""
    LIMIT_BYTES = 9.5 * 1024 * 1024 
    
    if len(file_bytes) <= LIMIT_BYTES:
        return file_bytes 
    
    print(f"⚠️ Файл слишком большой ({len(file_bytes) / 1024 / 1024:.2f} MB). Сжимаю...")
    
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            img = img.convert("RGB")
            
        max_dimension = 2560
        if max(img.size) > max_dimension:
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            
        output_io = io.BytesIO()
        img.save(output_io, format='JPEG', quality=85, optimize=True)
        return output_io.getvalue()
    except Exception as e:
        print(f"❌ Ошибка сжатия: {e}")
        return file_bytes

def normalize_image_urls(image_urls) -> list:
    """✅ ЕДИНАЯ функция нормализации URL"""
    if not image_urls:
        return []
    if isinstance(image_urls, str):
        return [image_urls]
    if isinstance(image_urls, list):
        return image_urls
    return []

def create_collage(images: list, max_size=1024) -> Image.Image:
    """
    Создаёт коллаж из 2-4 изображений
    
    2 фото: горизонтально [img1][img2]
    3-4 фото: сетка 2x2
    """
    count = len(images)
    
    if count == 2:
        cols, rows = 2, 1
    elif count <= 4:
        cols, rows = 2, 2
    else:
        raise ValueError("Max 4 images")
    
    cell_w = max_size // cols
    cell_h = max_size // rows
    
    canvas = Image.new('RGB', (max_size, max_size), 'white')
    
    for idx, img in enumerate(images):
        img_resized = img.copy()
        img_resized.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
        
        col = idx % cols
        row = idx // cols
        
        x = col * cell_w + (cell_w - img_resized.width) // 2
        y = row * cell_h + (cell_h - img_resized.height) // 2
        
        canvas.paste(img_resized, (x, y))
    
    return canvas

async def get_photo_url(bot: Bot, file_id: str) -> str:
    """Получает URL фото"""
    if not file_id:
        return None
    file_info = await bot.get_file(file_id)
    return f"https://api.telegram.org/file/bot{bot.token}/{file_info.file_path}"

# =====================================================================
# 🎛 КЛАВИАТУРЫ
# =====================================================================

def get_preflight_kb(model_type: str, ratio: str, quality: str):
    builder = InlineKeyboardBuilder()
    
    if model_type == "pro":
        model_btn = "💎 Модель: PRO"
    elif model_type == "nb2":
        model_btn = "🍌 Модель: Nano Banana 2"
    else:
        model_btn = "🍌 Модель: Standard"

    if model_type == "pro":
        if quality == "4k":
            qual_btn = "👑 Качество: 4K (до 10 мин)"
        elif quality == "2k":
            qual_btn = "🌟 Качество: 2K (1-5 мин)"
        else:
            qual_btn = "⚡️ Качество: HD (быстро)"
    elif model_type == "nb2":
        if quality == "4k":
            qual_btn = "👑 Качество: 4K"
        elif quality == "2k":
            qual_btn = "🌟 Качество: 2K"
        else:
            qual_btn = "⚡️ Качество: HD"
    else:
        qual_btn = None

    cost = calc_cost(model_type, quality)

    builder.button(text=model_btn, callback_data="pf_toggle_model")
    if qual_btn:
        builder.button(text=qual_btn, callback_data="pf_toggle_quality")
    builder.button(text=f"📐 Формат: {ratio}", callback_data="pf_select_ratio")
    builder.button(text=f"🚀 Запуск (спишем {cost} 🍌)", callback_data="pf_start")
    
    if model_type in ("pro", "nb2"):
        builder.adjust(1, 2, 1)
    else:
        builder.adjust(1, 1, 1)
        
    return builder.as_markup()

def get_ratio_kb(model_type: str = "standard"):
    builder = InlineKeyboardBuilder()
    
    if model_type == "nb2":
        ratios = ["1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4", "8:1", "9:16", "16:9", "21:9"]
    else:
        ratios = ["1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "21:9"]
    
    for r in ratios: 
        builder.button(text=r, callback_data=f"set_ratio_{r}")
    builder.button(text="🔙 Назад", callback_data="pf_back")
    
    if model_type == "nb2":
        builder.adjust(3, 3, 2, 2, 4, 1)
    else:
        builder.adjust(3, 3, 2, 2, 1)
    
    return builder.as_markup()

def get_cancel_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel_wizard")
    return builder.as_markup()

def get_result_kb(db_message_id: int, is_pro: bool, cost: int, is_nb2: bool = False):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"🔄 Ещё раз ({cost}🍌)", callback_data=f"reroll_{db_message_id}")
    builder.button(text=f"🎨 Изменить ({cost}🍌)", callback_data=f"edit_{db_message_id}")
    builder.button(text=f"🎬 Оживить фото (спишем {config.COST_VIDEO}🍌)", callback_data=f"animate_{db_message_id}")
    if is_pro or is_nb2:
        builder.button(text="📂 Скачать без сжатия", callback_data=f"download_{db_message_id}")
    builder.adjust(2, 1, 1) if (is_pro or is_nb2) else builder.adjust(2, 1)
    return builder.as_markup()

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
# 🛫 ПРЕДПОЛЕТНЫЙ ЧЕК
# =====================================================================
async def start_preflight_check(message: types.Message, state: FSMContext, prompt: str, image_urls=None, is_edit_mode=False):
    user_id = message.from_user.id

    # 🔥 ПРОВЕРЯЕМ РЕКЛАМНЫЙ СЦЕНАРИЙ
    data = await state.get_data()
    from_ad_scenario = data.get("from_ad_scenario", False)
    
    # Если пришли из рекламного сценария - используем его настройки
    if from_ad_scenario:
        scenario_prompt = data.get("ad_scenario_prompt")
        scenario_model = data.get("ad_scenario_model", "standard")
        scenario_ratio = data.get("ad_scenario_ratio", "1:1")
        
        # Объединяем промт пользователя с промтом сценария
        combined_prompt = f"{scenario_prompt}, {prompt}" if prompt else scenario_prompt
        
        # Очищаем флаг
        await state.update_data(from_ad_scenario=False)
        
        # Нормализуем URL
        normalized_urls = normalize_image_urls(image_urls)
        
        await state.update_data(
            pf_prompt=combined_prompt,
            pf_image_urls=normalized_urls,
            pf_model=scenario_model,
            pf_ratio=scenario_ratio,
            pf_quality="hd" if scenario_model == "nb2" else "2k"
        )
        
        if scenario_model == "pro": cost = config.COST_PRO_1K
        elif scenario_model == "nb2": cost = config.COST_NB2_1K
        else: cost = config.COST_STANDARD
        
        text = (
            f"🎨 <b>Параметры генерации</b>\n\n"  # 👈 <b> вместо **
            f"✨ Все готово!\n\n"
            f"<b>Жми \"🚀 Запуск\"</b>👇"  # 👈 <b> вместо **
        )
        
        await message.answer(
            text, 
            reply_markup=get_preflight_kb(scenario_model, scenario_ratio, "2k"), 
            parse_mode="HTML"
        )
        return
    
    # 🔥 ОБЫЧНАЯ ЛОГИКА
    force_pro = data.get("force_pro_mode", False)
    
    async with async_session() as session:
        pref_model = "pro" if force_pro else await get_user_model_preference(session, user_id)
    
    normalized_urls = normalize_image_urls(image_urls)
    
    await state.update_data(
        pf_prompt=prompt, 
        pf_image_urls=normalized_urls,
        pf_model=pref_model, 
        pf_ratio="1:1", 
        pf_quality="hd" if pref_model == "nb2" else "2k",
        pf_is_edit_mode=is_edit_mode,
    )
    await state.set_state(GenState.preflight_check)
    
    quality = "2k"  # дефолтное качество при инициализации
    if pref_model == "pro":
        if quality == "4k": cost = config.COST_PRO_4K
        elif quality == "2k": cost = config.COST_PRO_2K
        else: cost = config.COST_PRO_1K
    elif pref_model == "nb2":
        if quality == "4k": cost = config.COST_NB2_4K
        elif quality == "2k": cost = config.COST_NB2_2K
        else: cost = config.COST_NB2_1K
    else:
        cost = config.COST_STANDARD
    has_photo = normalized_urls is not None and len(normalized_urls) > 0
    if not has_photo or is_edit_mode:
        cost = calc_cost(pref_model, "hd")
        text = (
            f"⚙️ <b>Настройки генерации</b>\n\n"
            f"📝 <b>Запрос:</b> {prompt[:30]}...\n\n"
            f"💰 <b>Стоимость:</b> {cost} банан(а)\n\n"
            f"⚠️ <i>Внимание:</i> Нейросеть будет рисовать ИМЕННО ЭТОТ текст.\n\n"
            f"<b>Настрой параметры и жми \"🚀 Запуск\"</b> 👇"
        )
    else:
        cost = calc_cost(pref_model, "hd")
        text = (
            f"🎨 <b>Параметры генерации</b>\n\n"
            f"📝 <b>Запрос:</b> {prompt[:30]}...\n\n"
            f"💰 <b>Стоимость:</b> {cost} банан(а)\n\n"
            f"<b>Настрой параметры и жми \"🚀 Запуск\"</b> 👇"
        )
    
    await message.answer(text, reply_markup=get_preflight_kb(pref_model, "1:1", "hd"), parse_mode="HTML")

@router.callback_query(GenState.preflight_check, F.data == "pf_toggle_model")
async def cb_pf_toggle_model(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_model = data.get("pf_model", "standard")
    
    # Переключение по кругу: standard → nb2 → pro → standard
    if current_model == "standard":
        new_model = "nb2"
    elif current_model == "nb2":
        new_model = "pro"
    else:
        new_model = "standard"
    
    await state.update_data(pf_model=new_model)
    
    async with async_session() as session: 
        await set_user_model_preference(session, callback.from_user.id, new_model, manual=True)
    
    ratio = data.get("pf_ratio", "1:1")
    quality = data.get("pf_quality", "hd")
    
    # При переключении на nb2 сбрасываем качество на HD
    if new_model == "nb2":
        quality = "hd"
        await state.update_data(pf_quality="hd")
    
    if new_model == "pro":
        if quality == "4k": cost = config.COST_PRO_4K
        elif quality == "2k": cost = config.COST_PRO_2K
        else: cost = config.COST_PRO_1K
    elif new_model == "nb2":
        if quality == "4k": cost = config.COST_NB2_4K
        elif quality == "2k": cost = config.COST_NB2_2K
        else: cost = config.COST_NB2_1K
    else:
        cost = config.COST_STANDARD

    is_broadcast = data.get("is_broadcast_gen", False)
    has_photo = bool(data.get("pf_image_urls"))
    is_edit_mode = data.get("pf_is_edit_mode", False)
    
    if is_broadcast:
        text = (
            f"🎨 <b>Параметры генерации</b>\n\n"
            f"Выбери модель и жми <b>\"🚀 Запуск\"</b> 👇"
        )
    else:
        safe_prompt = data.get('pf_prompt', '')[:100].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        cost = calc_cost(new_model, quality)
        warning = ""
        if (not has_photo) or is_edit_mode:
            warning = f"⚠️ <i>Внимание:</i> Нейросеть будет рисовать ИМЕННО ЭТОТ текст.\n\n"
        text = (
            f"🎨 <b>Параметры генерации</b>\n\n"
            f"📝 <b>Запрос:</b> {safe_prompt}...\n\n"
            f"💰 <b>Стоимость:</b> {cost} банан(а)\n\n"
            f"{warning}"
            f"<b>Настрой параметры и жми \"🚀 Запуск\"</b> 👇"
        )
    
    await callback.message.edit_text(
        text, 
        reply_markup=get_preflight_kb(new_model, ratio, quality), 
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(GenState.preflight_check, F.data == "pf_toggle_quality")
async def cb_pf_toggle_quality(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_q = data.get("pf_quality", "2k")
    
    # ЦИКЛ: HD -> 2K -> 4K -> HD
    if current_q == "hd":
        new_q = "2k"
    elif current_q == "2k":
        new_q = "4k"
    else:
        new_q = "hd"
        
    await state.update_data(pf_quality=new_q)
    
    model = data.get("pf_model", "standard")
    ratio = data.get("pf_ratio", "1:1")
    
    if model == "pro":
        if new_q == "4k": cost = config.COST_PRO_4K
        elif new_q == "2k": cost = config.COST_PRO_2K
        else: cost = config.COST_PRO_1K
    elif model == "nb2":
        if new_q == "4k": cost = config.COST_NB2_4K
        elif new_q == "2k": cost = config.COST_NB2_2K
        else: cost = config.COST_NB2_1K
    else:
        cost = config.COST_STANDARD

    safe_prompt = data.get('pf_prompt', '')[:100].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    cost = calc_cost(model, new_q)
    has_photo = bool(data.get("pf_image_urls"))
    is_edit_mode = data.get("pf_is_edit_mode", False)
    warning = ""
    if (not has_photo) or is_edit_mode:
        warning = f"⚠️ <i>Внимание:</i> Нейросеть будет рисовать ИМЕННО ЭТОТ текст.\n\n"
    text = (
        f"🎨 <b>Параметры генерации</b>\n\n"
        f"📝 <b>Запрос:</b> {safe_prompt}...\n\n"
        f"💰 <b>Стоимость:</b> {cost} банан(а)\n\n"
        f"{warning}"
        f"<b>Настрой параметры и жми \"🚀 Запуск\"</b> 👇"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_preflight_kb(model, ratio, new_q),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(GenState.preflight_check, F.data == "pf_select_ratio")
async def cb_pf_select_ratio(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GenState.selecting_ratio)
    data = await state.get_data()
    model_type = data.get("pf_model", "standard")
    await callback.message.edit_text(
        "📐 **Выберите формат изображения:**", 
        reply_markup=get_ratio_kb(model_type), 
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(GenState.selecting_ratio, F.data == "pf_back")
async def cb_pf_ratio_back(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GenState.preflight_check)
    data = await state.get_data()
    model = data.get("pf_model")
    quality = data.get("pf_quality", "hd")
    if model == "pro":
        if quality == "4k": cost = config.COST_PRO_4K
        elif quality == "2k": cost = config.COST_PRO_2K
        else: cost = config.COST_PRO_1K
    elif model == "nb2":
        if quality == "4k": cost = config.COST_NB2_4K
        elif quality == "2k": cost = config.COST_NB2_2K
        else: cost = config.COST_NB2_1K
    else:
        cost = config.COST_STANDARD

    # 🔥 ПРОВЕРЯЕМ ФЛАГ BROADCAST 🔥
    is_broadcast = data.get("is_broadcast_gen", False)
    
    if is_broadcast:
        text = (
            f"🎨 <b>Параметры генерации</b>\n\n"
            f"Выбери модель и жми \"🚀 Запуск\" 👇"
        )
    else:
        safe_prompt = data.get('pf_prompt', '')[:100].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        cost = calc_cost(data.get("pf_model"), data.get("pf_quality"))
        has_photo = bool(data.get("pf_image_urls"))
        is_edit_mode = data.get("pf_is_edit_mode", False)
        warning = ""
        if (not has_photo) or is_edit_mode:
            warning = f"⚠️ <i>Внимание:</i> Нейросеть будет рисовать ИМЕННО ЭТОТ текст.\n\n"
        text = (
            f"🎨 <b>Параметры генерации</b>\n\n"
            f"📝 <b>Запрос:</b> {safe_prompt}...\n\n"
            f"💰 <b>Стоимость:</b> {cost} банан(а)\n\n"
            f"{warning}"
            f"<b>Настрой параметры и жми \"🚀 Запуск\"</b> 👇"
        )
    
    await callback.message.edit_text(
        text, 
        reply_markup=get_preflight_kb(
            data.get("pf_model"), 
            data.get("pf_ratio"), 
            data.get("pf_quality")
        ), 
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(GenState.selecting_ratio, F.data.startswith("set_ratio_"))
async def cb_pf_set_ratio(callback: types.CallbackQuery, state: FSMContext):
    new_ratio = callback.data.split("_")[2]
    await state.update_data(pf_ratio=new_ratio)
    await cb_pf_ratio_back(callback, state)

# 👇 ЗАМЕНИ ФУНКЦИЮ cb_pf_start НА ЭТУ 👇

@router.callback_query(GenState.preflight_check, F.data == "pf_start")
async def cb_pf_start(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    prompt = data.get("pf_prompt")
    image_urls = data.get("pf_image_urls")
    model_type = data.get("pf_model")
    ratio = data.get("pf_ratio")
    quality = data.get("pf_quality")
    
    if model_type == "pro":
        if quality == "4k": cost = config.COST_PRO_4K
        elif quality == "2k": cost = config.COST_PRO_2K
        else: cost = config.COST_PRO_1K
    elif model_type == "nb2":
        if quality == "4k": cost = config.COST_NB2_4K
        elif quality == "2k": cost = config.COST_NB2_2K
        else: cost = config.COST_NB2_1K
    else:
        cost = config.COST_STANDARD
    
    use_pro = (model_type == "pro")
    use_nb2 = (model_type == "nb2")
    
    # Логика разрешения
    resolution = "1K"
    if use_pro or use_nb2:
        if quality == "4k": resolution = "4K"
        elif quality == "2k": resolution = "2K"
    
    await callback.answer(f"🚀 Запускаю...", show_alert=False)
    
    await process_generation(
        callback.message, 
        callback.from_user.id, 
        prompt, 
        image_urls, 
        aspect_ratio=ratio, 
        cost=cost, 
        use_pro_model=use_pro,
        use_nb2_model=use_nb2,
        resolution=resolution,
        is_blend_mode=data.get("is_blend_mode", False),
        post_id=data.get("current_post_id")
    )

    from_retry_flow = data.get("force_pro_mode", False)
    if from_retry_flow:
        await state.update_data(force_pro_mode=False)
        
        from app.services.admin_logger import log_order_from_retry
        await log_order_from_retry(
            callback.bot,
            callback.from_user.id,
            cost,
            model_type
        )
    
    # ⚠️ ВАЖНО: Мы НЕ делаем await state.clear()
    # Состояние остается активным, чтобы кнопки в меню продолжали работать

# =====================================================================
# ВХОДНЫЕ ТОЧКИ
# =====================================================================
@router.message(F.chat.type == "private", F.media_group_id, StateFilter(GenState.free_mode, None, GenState.preflight_check, GenState.selecting_ratio, GenState.retry_waiting_photos))
async def handle_album_input(message: types.Message, state: FSMContext, bot: Bot, album: list[types.Message] = None):
    """Обработка альбомов (2-10 фото)"""

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
    
    messages = album if album else [message]
    count = len(messages)
    
    if count > 4:
        await message.answer("✋ **Ого, слишком много!**\nМаксимум 4 фото.", parse_mode="Markdown")
        return
    
    image_urls = []
    full_caption = ""
    
    for msg in messages:
        if msg.photo:
            url = await get_photo_url(bot, msg.photo[-1].file_id)
            if url:
                image_urls.append(url)
        if msg.caption and not full_caption: 
            full_caption = msg.caption
    
    if not image_urls:
        await message.answer("❌ Не удалось получить фото.")
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
        
        text = (
            f"🎨 <b>Параметры генерации</b>\n\n"  # 👈 <b> вместо **
            f"✨ Все готово!\n\n"
            f"<b>Жми \"🚀 Запуск\"</b>👇"  # 👈 <b> вместо **
        )
        await message.answer(
            text,
            reply_markup=get_preflight_kb(ad_scenario_model, ad_scenario_ratio, "2k"),
            parse_mode="Markdown"
        )
        return

    # 🔥 ЕСЛИ ЭТО BROADCAST - ИСПОЛЬЗУЕМ СОХРАНЁННЫЙ ПРОМПТ 🔥
    if is_from_broadcast and broadcast_prompt:
        await state.update_data(
        pf_prompt=broadcast_prompt,
        pf_image_urls=image_urls,
        pf_ratio=broadcast_ratio,
        pf_model=broadcast_model,  # 👈 ИСПОЛЬЗУЕМ МОДЕЛЬ ИЗ ПОСТА
        pf_quality="hd" if broadcast_model == "nb2" else "2k",
        is_broadcast_gen=True  # 👈 ДОБАВЬ ФЛАГ
    )
        await state.set_state(GenState.preflight_check)
    

        cost = calc_cost(broadcast_model, "hd" if broadcast_model == "nb2" else "2k")
    # 🔥 УПРОЩЁННОЕ СООБЩЕНИЕ ДЛЯ BROADCAST 🔥
        text = (
        f"🎨 *Параметры генерации*\n\n"
        f"Выбери модель и жми \"🚀 Запуск\"👇"
    )
        await message.answer(
        text,
        reply_markup=get_preflight_kb(broadcast_model, broadcast_ratio, "hd" if broadcast_model == "nb2" else "2k"),        parse_mode="Markdown"
    )
        return
    # 🔥 КОНЕЦ BROADCAST ЛОГИКИ 🔥
    
    # Обычный флоу
    if count == 1:
        if full_caption:
            # ... тут твой код для одного фото ...
            # (оставь как есть, если там всё работает)
            pass 
        else:
            await state.update_data(pending_image_urls=image_urls)
            await state.set_state(GenState.waiting_for_caption)
            await message.reply("📸 **Готово! Фото поймал.**\nНапиши, что с ним сделать?", parse_mode="Markdown")
            
    else:  # >= 2 фото
        await state.update_data(pending_image_urls=image_urls)
        
        # 1. Сначала проверяем, есть ли подпись
        if full_caption:
            # 🎬 Проверка на видео
            video_keywords = ["оживи", "оживить", "анимация", "видео", "video", "animate", "анимируй", "ожевить"]
            if any(keyword in full_caption.lower() for keyword in video_keywords):
                await send_video_offer_message(message, state, has_photo=True, photo_file_id=image_urls[0] if image_urls else None)
                return # 👈 ВАЖНО: выходим

            # 🚫 Проверка на ленивый промпт
            if is_lazy_prompt(full_caption):
                await send_lazy_prompt_message(message)
                return # 👈 ВАЖНО: выходим

            # ✅ Запускаем генерацию
            await start_preflight_check(message, state, full_caption, image_urls)
            return  # 🔥 САМОЕ ГЛАВНОЕ: ОСТАНАВЛИВАЕМ ФУНКЦИЮ ЗДЕСЬ 🔥

        # 2. Если подписи НЕТ нигде — просим задачу
        # Этот код выполнится ТОЛЬКО если full_caption пустой
        await state.set_state(GenState.waiting_for_caption)
        await message.answer(
            f"✅ **Получено {count} фото!**\nТеперь напиши задачу (например: «Смешай их»).", 
            parse_mode="Markdown"
        )


@router.message(F.text == "✨ Начать творить")
async def cmd_start_creating(message: types.Message, state: FSMContext):
    # Явно ставим состояние "свободный режим"
    await state.set_state(GenState.free_mode)
    
    text = (
        "*Я готов творить! 🎨*\n\n"
        "Пришли *от 1 до 4 фото* с описанием или напиши, что сделать.\n\n"
        "*Не знаешь, что создать? 👇*"
    )
    # Создаем inline-кнопку
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💃 Выбрать образ", url="https://t.me/+3ovTRpUPci85ODYy")]
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
    text = (
        "**Я готов творить!**\n"
        "Напиши, что создать, или пришли **от 1 до 4 фото**, которые нужно изменить или объединить 👇"
    )
    await callback.message.answer(text, parse_mode="Markdown")

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
    
    # Отправляем сообщение №2
    text = (
        "👌 <b>Договорились. Делаем качественно.</b>\n\n"
        "1. Пришлите <b>1-5 четких селфи</b> (дневной свет).\n"
        "2. Затем напишите запрос, добавив в него <b>правила для сохранения лица</b> 👆.\n\n"
        "Жду фото (а следом промпт) 👇"
    )
    
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
    await callback.message.delete()
    await callback.message.answer(
        "🏠 Главное меню",
        reply_markup=get_main_kb()
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
        # 🚫 ДОБАВЬ ЭТУ ПРОВЕРКУ 👇
        if is_lazy_prompt(message.caption):
            await send_lazy_prompt_message(message)
            return
        # 👆 КОНЕЦ ВСТАВКИ
        # Если есть подпись — сразу в настройки
        await start_preflight_check(message, state, message.caption, [url])
    else:
        # Если подписи нет — просим ввести
        await state.update_data(pending_image_urls=[url])
        await state.set_state(GenState.waiting_for_caption)
        await message.reply("📸 **Фото принято!** Напиши, что с ним сделать.", parse_mode="Markdown")

# 👆 КОНЕЦ ВСТАВКИ 👆

async def send_lazy_prompt_message(message: types.Message):
    """
    Отправляет сообщение-заглушку для ленивых промптов
    Экономит GPU и бананы пользователя
    """
    try:
        builder = InlineKeyboardBuilder()
        builder.button(
            text="💃 Примерить образ",
            url="https://t.me/+3ovTRpUPci85ODYy"
        )
        
        text = (
        "Хочу услышать от тебя <b>«ВАУ!»</b> 😍\n\n"
        "Но если делать без четкого описания, результат может разочаровать. "
        "А мы же хотим <b>шедевр</b>?\n\n"
        "Пожалуйста, <b>напиши подробнее</b>, что ты хочешь увидеть, или "
        "<b>выбери готовый образ:</b> 👇"
        )
        
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
        
        # Создаем кнопку поддержки
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="💬 Поддержка", url="https://t.me/nan0banana_help")
        builder.button(text="💃 Выбрать образ", url="https://t.me/+3ovTRpUPci85ODYy")
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
    main_prompt = data.get("param_main_prompt_template")
    if not main_prompt or not str(main_prompt).strip():
        await state.set_state(GenState.free_mode)
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Напишите ответ одним сообщением.")
        return
    final_prompt = apply_value_to_main_prompt(main_prompt, raw)
    ratio = data.get("broadcast_ratio", "1:1")
    model = data.get("broadcast_model", "standard")
    cached_fid = data.get("pending_param_photo_file_id")

    if cached_fid:
        await state.update_data(
            param_main_prompt_template=None,
            param_question_text=None,
            pending_param_photo_file_id=None,
        )
        await enter_broadcast_generation_preflight(
            message, state, message.bot,
            prompt=final_prompt,
            ratio=ratio,
            model=model,
            file_id=cached_fid,
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
    await message.answer(
        "🔥 <b>Отлично!</b>\n\n"
        "Теперь отправьте фото, чтобы увидеть себя в этом образе.",
        parse_mode="HTML",
    )


async def _cache_photo_while_waiting_for_prompt_text(
    message: types.Message, state: FSMContext, file_id: str
):
    data = await state.get_data()
    q = (data.get("param_question_text") or "").strip()
    if not q:
        await message.answer("Сначала ответьте на вопрос текстом.")
        return
    await state.update_data(pending_param_photo_file_id=file_id)
    await send_param_prompt_photo_before_text_error(message.bot, message.chat.id, q)


@router.message(GenState.waiting_for_prompt_text, F.photo)
async def handle_waiting_for_prompt_text_photo(message: types.Message, state: FSMContext):
    if message.media_group_id:
        return
    await _cache_photo_while_waiting_for_prompt_text(
        message, state, message.photo[-1].file_id
    )


@router.message(GenState.waiting_for_prompt_text, F.document)
async def handle_waiting_for_prompt_text_document(message: types.Message, state: FSMContext):
    if not _is_image_document(message):
        await message.answer("Пришлите текстовый ответ или изображение (фото / картинка файлом).")
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
    if not _is_image_document(message):
        await message.answer("Отправьте фото или изображение файлом.")
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
        "Сейчас нужно отправить <b>фото</b> для генерации.",
        parse_mode="HTML",
    )


@router.message(F.chat.type == "private", F.text, StateFilter(GenState.free_mode, None))
async def handle_free_text(message: types.Message, state: FSMContext):
    """Обработка текста без фото"""
    if message.text in IGNORED_TEXTS: 
        return
    
    # 🎬 ПЕРЕХВАТ СЛОВ ДЛЯ ВИДЕО
    video_keywords = ["оживи", "оживить", "анимация", "видео", "video", "animate","анимируй", "ожевить"]
    if any(keyword in message.text.lower() for keyword in video_keywords):
        await send_video_offer_message(message, state, has_photo=False)
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
    
    if not url:
        await message.answer("❌ Не удалось получить фото.")
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

# 🔥 УПРОЩЁННОЕ СООБЩЕНИЕ ДЛЯ BROADCAST 🔥
        cost = config.COST_STANDARD

        text = (
    f"🎨 *Параметры генерации*\n\n"
    f"Выбери модель и жми \"🚀 Запуск\"👇"
)
        await message.answer(
    text,
    reply_markup=get_preflight_kb(model, ratio, "1k"),  # 👈 только это
    parse_mode="Markdown"
)
        return
    
    if message.caption:
        # 🎬 ПЕРЕХВАТ СЛОВ ДЛЯ ВИДЕО (ПРОВЕРЯЕМ ПЕРВЫМ!)
        video_keywords = ["оживи", "оживить", "анимация", "видео", "video", "animate","анимируй", "ожевить"]
        if any(keyword in message.caption.lower() for keyword in video_keywords):
            await send_video_offer_message(message, state, has_photo=True, photo_file_id=message.photo[-1].file_id)
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

        await state.update_data(
            pending_image_urls=[url],
            pending_photo_file_id=message.photo[-1].file_id  # ← СОХРАНЯЕМ FILE_ID
        )
        await state.set_state(GenState.waiting_for_caption)

        await message.reply(
            "📸 **Фото принято!** Напиши, что с ним сделать.", 
            parse_mode="Markdown"
        )

@router.message(GenState.retry_waiting_photos, F.photo)
async def handle_retry_photos(message: types.Message, state: FSMContext, bot: Bot):
    """
    Обработка фото в режиме ретрая (после жалобы)
    """
    if message.media_group_id:
        return  # Обработается в handle_album_input
    
    url = await get_photo_url(bot, message.photo[-1].file_id)
    
    if message.caption:
        # Если есть подпись - сразу в preflight
        await state.update_data(pf_image_urls=[url])
        await start_preflight_check(message, state, message.caption, [url])
    else:
        # Если подписи нет - просим промпт
        await state.update_data(pending_image_urls=[url])
        await message.reply(
            "📸 <b>Фото принято!</b>\n"
            "Теперь напиши запрос с правилами сохранения лица.",
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
        await message.answer("❌ Сначала пришлите фото.")
        return
    
    await start_preflight_check(message, state, message.text, image_urls)

@router.message(GenState.waiting_for_caption, F.text)
async def handle_delayed_caption(message: types.Message, state: FSMContext):
    """Обработка отложенного текста после фото"""
    user_prompt = message.text

        # 🛡️ ФИЛЬТР КОНТЕНТА (добавь ПЕРЕД lazy_prompt)
    if await check_content_filter(message, message.text):
        return
    
    # 🎬 ПЕРЕХВАТ СЛОВ ДЛЯ ВИДЕО
    video_keywords = ["оживи", "оживить", "анимация", "видео", "video", "animate","анимируй", "ожевить"]
    if any(keyword in user_prompt.lower() for keyword in video_keywords):
        data = await state.get_data()
        file_id = data.get("pending_photo_file_id")
        await send_video_offer_message(message, state, has_photo=True, photo_file_id=file_id)
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
        await message.answer("❌ Ошибка: фото не найдены.")
        await state.clear()
        return
    
    await start_preflight_check(message, state, user_prompt, image_urls)

# =====================================================================
# ОБРАБОТКА РЕЗУЛЬТАТОВ
# =====================================================================
@router.callback_query(F.data.startswith("reroll_"))
async def cb_reroll(callback: types.CallbackQuery, bot: Bot):
    """Перегенерация с теми же параметрами"""
    await callback.answer("🔄 Запускаю...", show_alert=False)
    
    try:
        db_id = int(callback.data.split("_")[1])
        
        async with async_session() as session:
            history_item = await get_history_message_by_id(session, db_id)
        
        if not history_item or not history_item.content:
            await callback.message.answer("⚠️ Данные генерации устарели.")
            return
        
        params = json.loads(history_item.content)
        
        await callback.message.reply("🔄 **Ещё раз!**\nГенерирую...", parse_mode="Markdown")
        
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
            is_blend_mode=params.get("is_blend_mode", False)
        )
    except Exception as e:
        print(f"❌ Ошибка reroll: {e}")
        await callback.answer("❌ Ошибка перегенерации", show_alert=True)

@router.callback_query(F.data.startswith("download_video_"))
async def cb_download_video(callback: types.CallbackQuery, bot: Bot):
    await callback.answer("📥 Скачиваю оригинал...")
    
    task_id = callback.data.replace("download_video_", "")
    
    async with async_session() as session:
        from app.services.video_service import get_task_by_id
        task = await get_task_by_id(session, task_id)
        
        if not task or not task.result_video_url:
            await callback.answer("❌ Видео не найдено", show_alert=True)
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
                caption="📥 Оригинал без сжатия",
                disable_content_type_detection=True,  # ← Добавь эту строку
                request_timeout=300
            )
            
        except Exception as e:
            print(f"Ошибка скачивания видео: {e}")
            await bot.send_message(
                callback.from_user.id,
                f"💎 Не удалось загрузить файл напрямую. Вот ссылка на оригинал:\n{task.result_video_url}"
            )

@router.callback_query(F.data.startswith("download_"))
async def cb_download(callback: types.CallbackQuery, bot: Bot):
    await callback.answer("📥 Скачиваю оригинал...")
    
    try:
        db_id = int(callback.data.split("_")[1])
        async with async_session() as session: 
            history_item = await get_history_message_by_id(session, db_id)
        
        if not history_item:
            await callback.answer("❌ Запись не найдена.", show_alert=True)
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
                                caption="💎 Исходное качество (Original)"
                            )
                        else:
                            await callback.answer(f"Ошибка сервера IMG: {resp.status}", show_alert=True)
            except Exception as e:
                print(f"Ошибка скачивания: {e}")
                # Fallback: отправляем ссылку
                try:
                    await bot.send_message(
                        chat_id=callback.from_user.id,
                        text=f"💎 Не удалось загрузить файл напрямую. Вот ссылка на оригинал:\n{history_item.image_url}"
                    )
                except:
                    await callback.answer("❌ Не удалось получить файл.", show_alert=True)

        elif history_item.file_id:
            await bot.send_photo(
                chat_id=callback.from_user.id, 
                photo=history_item.file_id, 
                caption="📸 Копия из Telegram (Оригинал недоступен)"
            )
        else: 
            await callback.answer("❌ Файл потерян.", show_alert=True)

    except Exception as e:
        print(f"❌ Ошибка download: {e}")
        await callback.answer("❌ Ошибка загрузки", show_alert=True)


@router.callback_query(F.data.startswith("edit_"))
async def cb_edit_result(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """Редактирование существующего результата"""
    await callback.answer()
    
    try:
        db_id = int(callback.data.split("_")[1])
        
        async with async_session() as session: 
            history_item = await get_history_message_by_id(session, db_id)
        
        if not history_item or not history_item.file_id:
            await callback.answer("❌ Исходник не найден.", show_alert=True)
            return
        
        # Определяем стоимость из истории
        try: 
            params = json.loads(history_item.content)
            use_pro = params.get("pro", False)
            use_nb2 = params.get("nb2", False)
        except: 
            use_pro = False
            use_nb2 = False
        
        if use_pro: cost = config.COST_PRO_1K
        elif use_nb2: cost = config.COST_NB2_1K
        else: cost = config.COST_STANDARD
        
        await state.update_data(
            editing_file_id=history_item.file_id,
            edit_use_pro=use_pro,
            edit_cost=cost
        )
        await state.set_state(GenState.waiting_for_edit_instruction)
        
        await callback.message.reply(
            f"🎨 **Режим редактирования** ({cost}🍌)\n\n"   
            f"*Что изменить?*",
            reply_markup=get_cancel_kb(), 
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"❌ Ошибка edit: {e}")
        await callback.answer("❌ Ошибка редактирования", show_alert=True)

@router.message(GenState.waiting_for_edit_instruction, F.text)
async def handle_edit_instruction(message: types.Message, state: FSMContext, bot: Bot):
    """Обработка инструкции для редактирования"""
    instruction = message.text
    data = await state.get_data()
    file_id = data.get("editing_file_id")
    
    if not file_id:
        await message.answer("❌ Исходное фото не найдено.")
        await state.clear()
        return
    
    img_url = await get_photo_url(bot, file_id)
    
    if not img_url:
        await message.answer("❌ Не удалось получить фото.")
        await state.clear()
        return
    
    await start_preflight_check(message, state, instruction, [img_url], is_edit_mode=True)

# =====================================================================
# КОМАНДЫ
# =====================================================================
@router.message(Command("clear"))
async def cmd_clear_history(message: types.Message, state: FSMContext):
    """Очистка истории"""
    async with async_session() as session: 
        await clear_history(session, message.from_user.id)
    await state.clear()
    await message.answer("🧹 **Память очищена!**", parse_mode="Markdown")


@router.callback_query(F.data == "cancel_wizard")
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Отмена мастера"""
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено.")
    await callback.answer()

@router.callback_query(F.data.startswith("cat_"))
async def cb_select_category(callback: types.CallbackQuery, state: FSMContext):
    """Выбор категории генерации"""
    await callback.answer()
    
    category = callback.data.split("_")[1]
    await state.clear()
    await state.update_data(selected_category=category)
    
    if category == "pro":
        await state.set_state(GenState.free_mode)
        await callback.message.edit_text(
            "🌟 **Режим Nano Banana PRO**\n\n"
            "💎 **Цена:** 3 банана\n"
            "🚀 **Качество:** Ultra HD.\n\n"
            "✍️ Отправь запрос.", 
            parse_mode="Markdown"
        )
        return
    
    if category == "replace":
        await state.set_state(GenState.waiting_for_base_image)
        await callback.message.edit_text(
            "🖼 **Режим замены (Шаг 1/3)**\nПришли **фото-основу**.", 
            reply_markup=get_cancel_kb(), 
            parse_mode="Markdown"
        )
        return
    
    if category == "free":
        await state.set_state(GenState.free_mode)
        await callback.message.edit_text(
            "🎨 **Свободный режим**\n\nПиши текст или присылай фото.", 
            parse_mode="Markdown"
        )
    else:
        await state.set_state(GenState.waiting_for_category_input)
        await callback.message.edit_text(
            "✅ Выбран режим. Пришли фото или текст.", 
            parse_mode="Markdown"
        )

# ==============================================================================
# 🎨 BLEND DETECTOR
# ==============================================================================
BLEND_TRIGGERS = [
    "смешай", "смешать", "микс", "mix", "blend",
    "соедини", "соединить", "объедини", "объединить","обьедини","обьедени","объедени","обьединить","объеденить",
    "скрестить", "скрести", "составь","совмести"
    "вариация", "variation", "комбинируй", "combine",
    "слей", "merge", "креатив", "creative"
]

def is_blend_request(prompt: str) -> bool:
    """Проверяет, хочет ли пользователь смешивание (а не замену лица)"""
    prompt_lower = prompt.lower()
    return any(trigger in prompt_lower for trigger in BLEND_TRIGGERS)

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
    post_id: str = None

):
    """Основная функция генерации изображений"""
    bot = message.bot 
    
    # 1. Проверка и списание баланса
    async with async_session() as session:
        model_type = "pro" if use_pro_model else "nb2" if use_nb2_model else "standard"
        kie_credits = get_kie_credits(model_type, resolution)
        has_balance, transaction_id = await check_and_deduct_balance(session, user_id, amount=cost, post_id=post_id, model_type=model_type, kie_credits_cost=kie_credits)
        balance_left = await get_user_balance(session, user_id)

        if not has_balance:
            # 🔥 SMART ALERT: Определяем сценарий и показываем умное уведомление
            alert_text, alert_kb = await get_smart_alert_message(session, user_id, balance_left, cost)
            
            await message.answer(
                alert_text,
                reply_markup=alert_kb.as_markup(),
                parse_mode="HTML"
            )
            return

    # ✅ Нормализация URL
    final_urls = normalize_image_urls(image_urls)
    
    # 🔥 ОПРЕДЕЛЯЕМ СЦЕНАРИЙ: Простой vs Сложный
    is_complex_standard = (not use_pro_model and not use_nb2_model and len(final_urls) >= 2)
    # 🔥 ДЕТЕКТОР ЗАДАЧ ТИПА "ЗАМЕНА/ВСТАВКА"
    swap_keywords = [
        'поменя', 'замен', 'положи', 'помести', 'вставь', 'перенес', 
        'возьми', 'бери', 'со второ', 'из второ', 'с друго', 'из друго',
        'swap', 'replace', 'put', 'place', 'take from'
    ]
    is_swap_task = any(keyword in prompt.lower() for keyword in swap_keywords)
    # 🔥 ДЕТЕКТОР BLEND (СМЕШИВАНИЕ)
    is_blend_task = is_blend_mode or is_blend_request(prompt)

    # 🔥 AUTO-COLLAGE ТОЛЬКО ДЛЯ НЕ-SWAP ЗАДАЧ
    if is_complex_standard and len(final_urls) >= 2 and not is_swap_task and not is_blend_task:
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
                disable_notification=True  # 👈 БЕЗ ЗВУКА
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
            
        except Exception as e:
            print(f"⚠️ Ошибка создания коллажа: {e}")
            import traceback
            traceback.print_exc()
            # Продолжаем с оригинальными URL (fallback)
    
    # 2. Сообщение о старте (РАЗНОЕ для простого/сложного)
    if is_complex_standard:
        # 📌 СЦЕНАРИЙ Б: Сложный (Standard + много фото) - С ПРЕДУПРЕЖДЕНИЕМ
        wait_msg = await message.answer(
            "⏳ <b>Создаю...</b>\n\n"
            "⚠️ <b>Вы объединяете несколько фото в модели STANDARD.</b>\n"
            "Детали и сходство (особенно лица) могут искажаться.\n"
            "💡 <i>Для максимальной точности рекомендуем модель PRO.</i>",
            parse_mode="HTML"
        )
        should_delete_wait_msg = False  # НЕ УДАЛЯЕМ
    else:
        # 📌 СЦЕНАРИЙ А: Простой - ТОЛЬКО статус
        wait_msg = await message.answer("⏳ <b>Создаю...</b>", parse_mode="HTML")
        should_delete_wait_msg = True  # УДАЛЯЕМ

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)
        
        # 4. Генерация
        result_data = await generate_image(
            bot, prompt, final_urls, False, 
            aspect_ratio, use_pro_model, use_nb2_model, None, resolution
        )
        
        # 5. Обработка результата
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
            from sqlalchemy import update as sa_update
            async with async_session() as upd_session:
                await upd_session.execute(
                    sa_update(BananaTransaction)
                    .where(BananaTransaction.id == transaction_id)
                    .values(post_id=kie_task_id)
                )
                await upd_session.commit()
        
        if result_file:
            # 🔥 УДАЛЯЕМ СООБЩЕНИЕ ТОЛЬКО ДЛЯ ПРОСТОГО СЦЕНАРИЯ
            if should_delete_wait_msg:
                try: 
                    await wait_msg.delete()
                except: 
                    pass
            
            # 6. Формирование caption (итоговый вариант)
            caption = (
                f"🍌 <b>Готово!</b>\n"
                f"🔋 Осталось: <b>{balance_left}</b> 🍌\n\n"
                f"<b>P.S. Хочешь, чтобы лицо получалось один в один?</b> 👯‍♀️\n"
                f"👉 <a href='https://t.me/nanobanan_promt/12'><b>Секрет 100% сходства тут</b></a>\n\n"
                f"Сгенерировано в @nan0banana_bot"
            )
            
            # 7. Сжатие для превью
            file_bytes = result_file.data
            compressed_bytes = smart_compress_image(file_bytes)
            preview_file = types.BufferedInputFile(compressed_bytes, filename="result.png")
            
        # 8. Отправка
            sent_msg = None
            for attempt in range(3):
                try:
                    sent_msg = await message.answer_photo(
                        preview_file, 
                        caption=caption, 
                        parse_mode="HTML"
                    )
                    break
                except TelegramRetryAfter as e:
                    logger.warning(f"⏳ FloodWait при отправке фото: ждём {e.retry_after} сек")
                    await asyncio.sleep(e.retry_after)
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка отправки фото: {e}")
                    try:
                        sent_msg = await message.answer_document(
                            result_file, 
                            caption=caption, 
                            parse_mode="HTML"
                        )
                    except TelegramRetryAfter as e:
                        logger.warning(f"⏳ FloodWait при отправке документа: ждём {e.retry_after} сек")
                        await asyncio.sleep(e.retry_after)
                    except Exception as e:
                        logger.error(f"❌ Критическая ошибка отправки: {e}")
                    break

            # 9. Сохранение в БД
            if not sent_msg:
                return
            sent_file_id = (
                sent_msg.photo[-1].file_id if sent_msg.photo 
                else sent_msg.document.file_id
            )

            await log_generation(
                bot, 
                message.chat,
                prompt=prompt, 
                model="PRO" if use_pro_model else "NB2" if use_nb2_model else "Standard",
                photo_file_id=sent_file_id
            )
            
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
            
            async with async_session() as session:
                await add_history(
                    session, user_id, "user", prompt, 
                    has_image=bool(final_urls)
                )
                await increment_generations_count(session, user_id)

                
                model_type = "pro" if use_pro_model else "nb2" if use_nb2_model else "standard"
                kie_credits = get_kie_credits(model_type, resolution)


                model_msg = await add_history(
                    session, user_id, "model", meta_data, 
                    has_image=True, 
                    file_id=sent_file_id, 
                    image_url=source_url
                )
                db_id = model_msg.id
            
            # 10. Добавление кнопок
            if db_id:
                await sent_msg.edit_reply_markup(
                    reply_markup=get_result_kb(db_id, use_pro_model, cost, is_nb2=use_nb2_model)
                )
            
            async with async_session() as session:
                user = await get_user(session, user_id)
                
                # Если это первая генерация пользователя
                if user and not user.first_generation_done:
                    user.first_generation_done = True
                    await session.commit()
                    
                    # Если у него есть реферер - начисляем бонус
                    if user.referrer_id:
# ======================================================
                        # 🕒 БЕЗОПАСНАЯ ПРОВЕРКА ДАТЫ (Smart Fix)
                        # ======================================================
                        # 1. Берем текущее время (в UTC)
                        now_utc = datetime.now(timezone.utc)
                        
                        # 2. Берем дату регистрации
                        reg_date = user.created_at
                        
                        # 3. ГЛАВНЫЙ ФИКС: Если дата "голая" (без зоны), даем ей UTC
                        if reg_date.tzinfo is None:
                            reg_date = reg_date.replace(tzinfo=timezone.utc)
                            
                        # 4. Теперь вычитаем (ошибки не будет)
                        days_since_creation = (now_utc - reg_date).days
                        # ======================================================
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
                                builder.button(text="🤝 Пригласить ещё", callback_data="goto_free")
                                
                                # Отправляем уведомление реферу
                                await bot.send_message(
                                    user.referrer_id,
                                    f"🥳 Ура! По твоей ссылке пришел друг.\n"
                                    f"Баланс пополнен: <b>+2 банана</b> 🍌\n\n"
                                    f"Всего на счету: <b>{new_balance}</b>",
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
        else:
            # ❌ NULL ОТВЕТ - ВОЗВРАТ ДЕНЕГ
            print("❌ API вернул NULL")

            await log_error(
                bot, 
                user_id,               # ✅ Берем ID из аргумента функции (он точный)
                message.chat.username, # ✅ Берем юзернейм из чата
                prompt, 
                error_text="API returned NULL (Blocked?)"
            )

            async with async_session() as session: 
                await admin_change_balance(session, user_id, cost)
            # Логируем возврат
            from app.services.admin_logger import log_banana_refund
            await log_banana_refund(bot, user_id, message.chat.username, cost, "API вернул NULL (Blocked?)")
            
            try: 
                await wait_msg.edit_text(
                    "❌ <b>Ошибка генерации</b>\n\n"
                    "API не смог создать изображение.\n"
                    f"💰 {cost} 🍌 возвращены на баланс.",
                    parse_mode="HTML"
                )
            except: 
                await message.answer(
                    "❌ <b>Ошибка генерации</b>\n\n"
                    "API не смог создать изображение.\n"
                    f"💰 {cost} 🍌 возвращены на баланс.",
                    parse_mode="HTML"
                )
                
    except Exception as e:
        # 1. Логируем ошибку в консоль и админу
        print(f"❌ Критическая ошибка: {e}")
        
        # Отправляем в канал логов (чтобы ты видел реальную причину)
        await log_error(
            bot, 
            user_id,               
            message.chat.username, 
            prompt, 
            error_text=f"CRASH: {str(e)[:100]}"
        )
        
        # 2. Возвращаем деньги
        async with async_session() as session: 
            await admin_change_balance(session, user_id, cost)
        # Логируем возврат
        from app.services.admin_logger import log_banana_refund
        await log_banana_refund(bot, user_id, message.chat.username, cost, f"Ошибка генерации: {str(e)[:50]}")
        
# 3. 🛡️ ПЕРЕВОДЧИК ОШИБОК ДЛЯ ПОЛЬЗОВАТЕЛЯ
        err_msg = str(e).lower()

        # Лог в консоль для отладки
        print(f"❌ API ERROR: {err_msg}")

        # --- ГРУППА 1: Цензура и контент ---
        if any(x in err_msg for x in ["sensitive", "nsfw", "safety", "banned", "content found", "violated", "policy", "prohibited"]):
            # Предполагается, что функция log_security_ban у тебя уже определена
            await log_security_ban(bot, user_id, message.chat.username, prompt, source="API Filter")
            user_friendly_text = (
                "🔞 <b>Сработал фильтр безопасности!</b>\n\n"
                "Нейросеть отказывается это генерировать (18+, насилие или другие запрещенные темы).\n"
                "🍌 <i>Попробуй сделать описание более пушистым и безопасным.</i>"
            )

        # --- ГРУППА 2: Ошибки ввода пользователя (422) ---
        elif "422" in err_msg or "validation error" in err_msg:
            user_friendly_text = (
                "📏 <b>Слишком сложный или кривой запрос.</b>\n\n"
                "Возможно, текст слишком длинный, или выбраны несовместимые настройки.\n"
                "🍌 <i>Упрости запрос и попробуй еще раз.</i>"
            )

            # --- ГРУППА 2.5: Специфичная ошибка Gemini (Неудачный промпт) ---
        elif "gemini could not generate" in err_msg or "different prompt" in err_msg:
            user_friendly_text = (
                "🎨 <b>Не удалось сгенерировать по этому описанию.</b>\n\n"
                "Нейросеть запуталась в деталях и не смогла собрать картинку.\n"
                "🍌 <i>Просто повторять запрос нет смысла. Пожалуйста, измени формулировку промпта (добавь деталей или, наоборот, упрости) и попробуй снова!</i>"
            )

            # --- ГРУППА 2.6: Публичная личность (Kie REJECT specific) ---
        elif (
            "kie reject" in err_msg
            and "request blocked" in err_msg
            and ("prominent public figure" in err_msg or "public figure" in err_msg)
        ):
            user_friendly_text = (
                "👤 <b>Запрос отклонён: публичная личность</b>\n\n"
                "Похоже, на фото или в описании есть узнаваемая публичная персона.\n"
                "По правилам провайдера такую генерацию выполнить нельзя.\n"
                "🍌 <i>Попробуй другое фото без знаменитостей или измени запрос без упоминания публичных людей.</i>"
            )

            # --- ГРУППА 2.7: Отказ провайдера (Kie REJECT общий) ---
        elif "kie reject" in err_msg or "failed to generate image" in err_msg:
            user_friendly_text = (
                "🛑 <b>Генерация прервана.</b>\n\n"
                "Нейросеть отклонила этот запрос. Возможно, в описании есть конфликтные детали или скрытые триггеры.\n"
                "🍌 <i>Пожалуйста, попробуй перефразировать промпт.</i>"
            )

            # --- ГРУППА 2.8: Протухшая ссылка на файл в Telegram (404 Client Error) ---
        elif "api.telegram.org/file/" in err_msg and "404" in err_msg:
            user_friendly_text = (
                "🖼 <b>Исходная картинка потерялась!</b>\n\n"
                "Telegram хранит временные ссылки на файлы всего 1 час, и время вышло.\n"
                "🍌 <i>Пожалуйста, отправь свою фотографию заново и повтори запрос.</i>"
            )

            # --- ГРУППА 2.9: Мягкий отказ нейросети (Copilot / DALL-E / Bing) ---
        elif any(x in err_msg for x in ["unable to help you with that", "对不起", "generation failed: sorry"]):
            user_friendly_text = (
                "🛑 <b>Нейросеть вежливо отказалась.</b>\n\n"
                "Запрос отклонён. Обычно это происходит, если в описании есть защищенные авторским правом персонажи (Бэтмен, Микки Маус и т.д.) или сработал скрытый фильтр.\n"
                "🍌 <i>Жать повтор нет смысла. Попробуй заменить имена собственные на общие описания (например, «супергерой в черном плаще») и отправь снова.</i>"
            )

        # --- ГРУППА 3: Временные проблемы на сервере (Попробуй позже) ---
        # Добавили 502 и 503 (Ошибки шлюзов и AI Studio)
        elif any(x in err_msg for x in ["429", "455", "500", "501", "502", "503", "internal", "reject", "timeout", "busy", "queue"]):
            user_friendly_text = (
                "⏳ <b>Серверы сейчас перегружены.</b>\n\n"
                "Обезьянки, крутящие педали нейросетей, немного устали от наплыва задач.\n"
                "🍌 <i>Обычно это проходит за пару минут. Дай им передохнуть и жми снова!</i>"
            )

        # --- ГРУППА 4: Критические ошибки (401, 402, 404, 505) ---
        elif any(x in err_msg for x in ["401", "402", "404", "505", "unauthorized", "insufficient credits"]):
            user_friendly_text = (
                "🛠 <b>Техническое обслуживание!</b>\n\n"
                "Мы прямо сейчас полируем механизмы и обновляем связи с нейросетями.\n"
                "🍌 <i>Скоро всё снова заработает, спасибо за терпение.</i>"
            )
            # # Обязательно шлем алерт тебе!
            # # Убедись, что переменная ADMIN_ID задана (твой Telegram ID)
            # try:
            #     await bot.send_message(
            #         ADMIN_IDS, 
            #         f"🚨 <b>АЛЯРМ в Nano Banana!</b>\nОшибка оплаты, лимитов или ключа API!\n\n<code>{err_msg}</code>",
            #         parse_mode="HTML"
            #     )
            # except Exception as admin_e:
            #     print(f"Не удалось отправить алерт админу: {admin_e}")

        # --- ГРУППА 5: Всё остальное (Неизвестная ошибка) ---
        else:
            user_friendly_text = (
                "⚠️ <b>Произошла банановая аномалия.</b>\n\n"
                "Неизвестная ошибка. Мы уже получили отчет и начали расследование.\n"
                "🍌 <i>Попробуй повторить попытку чуть позже.</i>"
            )
            
        # И в конце отправляем user_friendly_text пользователю:
        # await message.answer(user_friendly_text, parse_mode="HTML")

        # 4. Финал: Отправка сообщения + Возврат средств
        final_text = f"{user_friendly_text}\n\n💰 <b>{cost} 🍌 возвращены на баланс.</b>"

        try: 
            await wait_msg.edit_text(final_text, parse_mode="HTML")
        except: 
            await message.answer(final_text, parse_mode="HTML")
@router.callback_query(F.data.regexp(r"^bc_\d+$"))  # только bc_123, не bc_model_
async def cb_broadcast_generate(callback: types.CallbackQuery, state: FSMContext):
    """Обработка нажатия кнопки генерации из рассылки"""
    
    broadcast_id = int(callback.data.split("_")[1])
    
    async with async_session() as session:
        result = await session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )
        broadcast = result.scalar_one_or_none()

        if not broadcast:
            await callback.answer("⚠️ Рассылка не найдена", show_alert=True)
            return

        pq = (broadcast.param_question or "").strip()
        has_param_question = bool(pq)

        if not broadcast.hidden_prompt:
            await callback.answer("⚠️ Ошибка: промпт не найден", show_alert=True)
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
        )
        await state.set_state(GenState.waiting_for_prompt_text)
        await send_param_prompt_text_intro(callback.bot, callback.from_user.id, pq_clean)
        await callback.answer()
        return

    await state.update_data(
        broadcast_prompt=broadcast.hidden_prompt,
        broadcast_ratio=broadcast.aspect_ratio or "1:1",
        broadcast_model=broadcast.model_type or "standard",
        from_broadcast=True
    )
    await state.set_state(GenState.free_mode)

    await callback.message.answer(
        f"🔥 <b>Отлично!</b>\n\n"
        f"Отправьте фото, чтобы увидеть себя в этом образе.\n\n"
        f"💡 <i>Промпт уже применен - просто пришлите фото!</i>",
        parse_mode="HTML"
    )

    await callback.answer()

    # =====================================================================
# 🎬 VIDEO GENERATION HANDLERS
# =====================================================================

async def send_video_offer_message(message: types.Message, state: FSMContext, has_photo: bool = False, photo_file_id: str = None):
    """
    Отправляет предложение создать видео
    """
    builder = InlineKeyboardBuilder()
    
    # Добавляем file_id в callback_data если есть
    # Всегда используем просто "video_start"
    # file_id сохраним в state
    callback_data = "video_start"
    
    builder.button(text=f"🎬 Оживить фото (спишем {config.COST_VIDEO}🍌)", callback_data=callback_data)
    builder.button(text="❌ Отмена", callback_data="video_cancel")
    builder.adjust(1)
    
    text = (
        "О, ты хочешь видео! 🎬\n\n"
        f"Этот режим создает только картинки. А магия оживления стоит <b>{config.COST_VIDEO} 🍌</b>\n\n"
        "Я использую лучшую нейросеть мира — <b>Kling AI</b>, поэтому качество будет киношное! 🔥\n\n"
        "Сделаем видео? 👇"
    )
    
    if has_photo and photo_file_id:
        await state.update_data(pending_video_photo=photo_file_id)
    
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
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
        # НЕ очищаем state здесь! Пусть process_video_generation сам решает
        await process_video_generation(
            callback.message, 
            callback.from_user.id, 
            photo_file_id, 
            state, 
            username=callback.from_user.username
        )
    else:
        # Фото нет - просим прислать
        await state.set_state(GenState.waiting_for_video_source)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Отмена", callback_data="video_cancel")
        
        await callback.message.answer(
            "Супер! Чтобы начать магию, пришли мне фотографию, которую нужно оживить 📸",
            reply_markup=builder.as_markup()
        )
@router.callback_query(F.data == "video_cancel")
async def cb_video_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Отмена создания видео"""
    await state.clear()
    await state.update_data(pending_video_photo=None)
    
    await callback.message.answer(
        "Понял, магию отложим 😊\n\n"
        "Я готов создавать обычные изображения! Жду твой промт или фото с описанием 👇"
    )
    
    await callback.answer()

@router.message(GenState.waiting_for_video_source, F.photo)
async def handle_video_source_photo(message: types.Message, state: FSMContext):
    """Обработка фото для генерации видео"""
    photo_file_id = message.photo[-1].file_id
    await state.clear()  # ← ДОБАВЬ! Очищаем состояние

    await process_video_generation(message, message.from_user.id, photo_file_id, state, username=message.from_user.username)
@router.callback_query(F.data.startswith("animate_"))
async def cb_animate_result(callback: types.CallbackQuery, state: FSMContext):
    """Оживление существующего результата"""
    await callback.answer()
    
    try:
        db_id = int(callback.data.split("_")[1])
        
        async with async_session() as session:
            history_item = await get_history_message_by_id(session, db_id)
        
        if not history_item or not history_item.file_id:
            await callback.answer("❌ Исходник не найден.", show_alert=True)
            return
        
        photo_file_id = history_item.file_id
        await process_video_generation(callback.message, callback.from_user.id, photo_file_id, state, from_result_button=True, username=callback.from_user.username)        
    except Exception as e:
        print(f"❌ Ошибка animate: {e}")
        await callback.answer("❌ Ошибка запуска генерации видео", show_alert=True)

async def process_video_generation(message: types.Message, user_id: int, photo_file_id: str, state: FSMContext, from_result_button: bool = False, username: str = None):
    """
    Основная функция обработки генерации видео
    """
    from app.services.video_service import create_video_generation_task
    from app.services.user_service import check_and_deduct_balance, get_user_balance
    
    COST = config.COST_VIDEO
    
    # Проверка баланса
    async with async_session() as session:
        has_balance, _ = await check_and_deduct_balance(session, user_id, amount=COST)
        balance_left = await get_user_balance(session, user_id)

        if not has_balance:
            alert_text, alert_kb = await get_smart_alert_message(session, user_id, balance_left, COST)
            await message.answer(alert_text, reply_markup=alert_kb.as_markup(), parse_mode="HTML")
            return
    
# Списали деньги - запускаем генерацию
    if from_result_button:
        wait_text = (
            "🚀 <b>Запускаю Kling AI!</b> (лучшую нейронку в мире)\n\n"
            "Делаю для тебя киношное качество. Видео будет готово через 3-10 минут. Жди шедевр! 🔥"
        )
    else:
        wait_text = "⏳ <b>Магия началась!</b> Видео будет готово через 3-10 минут"
    
    wait_msg = await message.answer(wait_text, parse_mode="HTML")
    
    # Webhook URL
    webhook_url = "https://aaa123.site/kling_webhook"  # ← Меняй с ngrok на прод
    
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
                from app.services.user_service import admin_change_balance
                await admin_change_balance(session, user_id, COST)
                # Логируем возврат
            from app.services.admin_logger import log_banana_refund
            await log_banana_refund(message.bot, user_id, username, COST, "Сервис генерации видео недоступен")
            
            await wait_msg.edit_text(
                f"😔 <b>Упс, сервис генерации видео временно недоступен</b>\n\n"
                f"Попробуйте через пару минут — обычно всё быстро восстанавливается! ⏰\n\n"
                f"💰 {COST} 🍌 возвращены на баланс",
                parse_mode="HTML"
            )
    
    except Exception as e:
        print(f"❌ Ошибка process_video_generation: {e}")
        
        # Возвращаем деньги
        async with async_session() as session:
            from app.services.user_service import admin_change_balance
            await admin_change_balance(session, user_id, COST)
        # Логируем возврат
        from app.services.admin_logger import log_banana_refund
        await log_banana_refund(message.bot, user_id, username, COST, f"Ошибка запуска видео: {str(e)[:50]}")
        
        await wait_msg.edit_text(
            f"😔 Произошла ошибка при запуске.\n\n"
            f"💰 {COST} 🍌 возвращены на баланс",
            parse_mode="HTML"
        )
    
    finally:
        # Очищаем pending_video_photo только после успешного старта
        if 'result' in locals() and result.get("success"):
            await state.update_data(pending_video_photo=None)

@router.callback_query(F.data.startswith("reanimate_"))
async def cb_reanimate_video(callback: types.CallbackQuery, state: FSMContext):
    """Повторная генерация видео (ещё раз)"""
    await callback.answer("🔄 Запускаю...", show_alert=False)
    
    try:
        task_id = callback.data.split("_", 1)[1]
        
        # Получаем исходную задачу из БД
        async with async_session() as session:
            from app.services.video_service import get_task_by_id
            original_task = await get_task_by_id(session, task_id)
        
        if not original_task or not original_task.source_image_file_id:
            await callback.answer("❌ Исходник не найден.", show_alert=True)
            return
        
        # Запускаем новую генерацию с тем же фото
        await process_video_generation(
            callback.message,
            callback.from_user.id,
            original_task.source_image_file_id,
            state,
            username=callback.from_user.username
        )
        
    except Exception as e:
        print(f"❌ Ошибка reanimate: {e}")
        await callback.answer("❌ Ошибка запуска", show_alert=True)

@router.message(F.text, StateFilter(GenState.free_mode))
async def handle_text_in_free_mode(message: types.Message, state: FSMContext):
    """Обработка текста когда ожидаем фото"""
    
    data = await state.get_data()
    
    # 🔥 ЕСЛИ ЭТО ПОЛЬЗОВАТЕЛЬ ИЗ РЕКЛАМЫ
    if data.get('from_ad_scenario'):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_generation")]
        ])
        
        await message.answer(
            "📸 <b>Пришлите ваше фото для обработки</b>\n\n"
            "Настройки уже применены, осталось только прислать фото! 👇\n\n"
            "Или нажмите «Отменить», чтобы вернуться в главное меню.",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        return

@router.callback_query(F.data == "cancel_generation")
async def callback_cancel_generation(callback: CallbackQuery, state: FSMContext):
    """Отмена генерации и возврат в главное меню"""
    await state.clear()
    
    await callback.message.edit_text(
        "❌ <b>Отменено</b>\n\n"
        "Используйте кнопки ниже для продолжения 👇",
        parse_mode="HTML"
    )
    
    await callback.answer()
