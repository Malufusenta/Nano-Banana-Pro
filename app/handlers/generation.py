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
from app.services.admin_logger import log_generation, log_error, log_lazy_prompt_interceptor
from app.models import Broadcast  # 👈 Добавь Broadcast
from app.database import async_session
from app.services.user_service import (
    check_and_deduct_balance, get_user_balance, is_user_premium, 
    add_history, clear_history, get_history_message_by_id, get_dialog_context,
    start_generation_task, finish_generation_task, admin_change_balance,
    get_user_model_preference, set_user_model_preference, has_user_purchased
)
from app.services.ai_engine import generate_image
from app.utils import prompts
from app.utils.prompt_validator import is_lazy_prompt
from app import config

router = Router()

COMPLAINT_INSTRUCTION_PHOTO = "AgACAgIAAxkBAAIEXmljYVg9EmWKVeMfvkyswZTdlygIAALjDWsb9o0ZS5z4N3QcX6nOAQADAgADeQADOAQ"  # 👈 Вставь свой file_id


# 👇 ЗАМЕНИТЬ ВЕСЬ СПИСОК IGNORED_TEXTS НА ЭТОТ:
IGNORED_TEXTS = [
    "✨ Начать творить", "🎨 Создать изображение", "Заработать🍌", "📚 Гайд",
    "📸 Примеры работ", "👤 Профиль", "👤 Мой профиль", "💬 Поддержка",
    "🍌 Купить бананы", "Фарминг🍌", "ℹ️ О нас", "ℹ️ Что умеет бот?",
    "/start", "/help", "/admin", "/stats", "/clear"
    # 👇 КОМАНДЫ БОКОВОГО МЕНЮ
    "/start", "/help", "/admin", "/stats", "/clear",
    "/profile", "/free", "/about", "/support", "/guide"
]

async def get_smart_alert_message(user_id: int, balance: int, cost: int) -> tuple[str, InlineKeyboardBuilder]:
    """
    Возвращает умное сообщение и клавиатуру в зависимости от сценария
    
    Returns:
        (text, keyboard_builder)
    """
    async with async_session() as session:
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

@router.message(F.photo)
async def get_photo_id(message: types.Message):
    file_id = message.photo[-1].file_id
    await message.answer(f"<code>{file_id}</code>", parse_mode="HTML")

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
    
    model_btn = "💎 Модель: PRO" if model_type == "pro" else "🍌 Модель: Standard"
    builder.button(text=model_btn, callback_data="pf_toggle_model")
    builder.button(text=f"📐 Формат: {ratio}", callback_data="pf_select_ratio")
    
    if model_type == "pro":
        # Логика подписи кнопки
        if quality == "4k":
            qual_btn = "👑 Качество: 4K"
        elif quality == "2k":
            qual_btn = "🌟 Качество: 2K"
        else:
            qual_btn = "⚡️ Качество: HD"
            
        builder.button(text=qual_btn, callback_data="pf_toggle_quality")
    
    cost = config.COST_PRO if model_type == "pro" else config.COST_STANDARD
    builder.button(text=f"🚀 Сгенерировать ({cost}🍌)", callback_data="pf_start")
    
    builder.adjust(2, 1, 1) if model_type == "pro" else builder.adjust(2, 1)
    return builder.as_markup()

def get_ratio_kb():
    builder = InlineKeyboardBuilder()
    ratios = ["1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "21:9"]
    for r in ratios: 
        builder.button(text=r, callback_data=f"set_ratio_{r}")
    builder.button(text="🔙 Назад", callback_data="pf_back")
    builder.adjust(3, 3, 2, 2, 1)
    return builder.as_markup()

def get_cancel_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel_wizard")
    return builder.as_markup()

def get_result_kb(db_message_id: int, is_pro: bool, cost: int):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"🔄 Ещё раз ({cost}🍌)", callback_data=f"reroll_{db_message_id}")
    builder.button(text=f"🎨 Изменить ({cost}🍌)", callback_data=f"edit_{db_message_id}")
    if is_pro:
        builder.button(text="📂 Скачать без сжатия", callback_data=f"download_{db_message_id}")
    builder.adjust(2, 1) if is_pro else builder.adjust(2)
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
async def start_preflight_check(message: types.Message, state: FSMContext, prompt: str, image_urls=None):
    user_id = message.from_user.id

    # 🔥 FORCE PRO LOGIC - Проверяем флаг перед загрузкой настроек
    data = await state.get_data()
    force_pro = data.get("force_pro_mode", False)
    
    async with async_session() as session:
        pref_model = await get_user_model_preference(session, user_id)
    async with async_session() as session:
        pref_model = "pro" if force_pro else await get_user_model_preference(session, user_id)
    
    # ✅ Нормализуем URL
    normalized_urls = normalize_image_urls(image_urls)
    
    await state.update_data(
        pf_prompt=prompt, 
        pf_image_urls=normalized_urls,  # ✅ Всегда список
        pf_model=pref_model, 
        pf_ratio="1:1", 
        pf_quality="2k"
    )
    await state.set_state(GenState.preflight_check)
    
    cost = config.COST_PRO if pref_model == "pro" else config.COST_STANDARD
    text = (
        f"🎨 *Параметры генерации*\n\n"
        f"📝 **Запрос:** {prompt[:100]}...\n"
        f"💰 **Стоимость:** {cost} банан(а)\n\n"
        f"*Настрой параметры и жми \"Сгенерировать\"*👇"  # ✅ ЖИРНЫЙ + КАВЫЧКИ
    )
    await message.answer(text, reply_markup=get_preflight_kb(pref_model, "1:1", "hd"), parse_mode="Markdown")

@router.callback_query(GenState.preflight_check, F.data == "pf_toggle_model")
async def cb_pf_toggle_model(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_model = data.get("pf_model", "standard")
    new_model = "pro" if current_model == "standard" else "standard"
    
    await state.update_data(pf_model=new_model)
    
    async with async_session() as session: 
        await set_user_model_preference(session, callback.from_user.id, new_model)
    
    ratio = data.get("pf_ratio", "1:1")
    quality = data.get("pf_quality", "hd")
    cost = config.COST_PRO if new_model == "pro" else config.COST_STANDARD

    # 🔥 ПРОВЕРЯЕМ ФЛАГ BROADCAST 🔥
    is_broadcast = data.get("is_broadcast_gen", False)
    
    if is_broadcast:
        # Упрощённый текст для broadcast
        text = (
            f"🎨 *Параметры генерации*\n\n"
            f"Выбери модель и жми *\"Сгенерировать\"*👇"
        )
    else:
    
        text = (
        f"🎨 *Параметры генерации*\n\n"
        f"📝 **Запрос:** {data.get('pf_prompt', '')[:100]}...\n"
        f"💰 **Стоимость:** {cost} банан(а)\n\n"
        f"*Настрой параметры и жми \"Сгенерировать\"*👇"  # ✅ ЖИРНЫЙ + КАВЫЧКИ
    )
    
    await callback.message.edit_text(
        text, 
        reply_markup=get_preflight_kb(new_model, ratio, quality), 
        parse_mode="Markdown"
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
    
    await callback.message.edit_reply_markup(reply_markup=get_preflight_kb(model, ratio, new_q))
    await callback.answer()

@router.callback_query(GenState.preflight_check, F.data == "pf_select_ratio")
async def cb_pf_select_ratio(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GenState.selecting_ratio)
    await callback.message.edit_text(
        "📐 **Выберите формат изображения:**", 
        reply_markup=get_ratio_kb(), 
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(GenState.selecting_ratio, F.data == "pf_back")
async def cb_pf_ratio_back(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GenState.preflight_check)
    data = await state.get_data()
    cost = config.COST_PRO if data.get("pf_model") == "pro" else config.COST_STANDARD

    # 🔥 ПРОВЕРЯЕМ ФЛАГ BROADCAST 🔥
    is_broadcast = data.get("is_broadcast_gen", False)
    
    if is_broadcast:
        # Упрощённый текст для broadcast
        text = (
            f"🎨 *Параметры генерации*\n\n"
            f"Выбери модель и жми \"Сгенерировать\"👇"
        )
    else:
    
        text = (
        f"🎨 *Параметры генерации*\n\n"
        f"📝 **Запрос:** {data.get('pf_prompt', '')[:100]}...\n"
        f"💰 **Стоимость:** {cost} банан(а)\n\n"
        f"*Настрой параметры и жми \"Сгенерировать\"*👇"  # ✅ ЖИРНЫЙ + КАВЫЧКИ
    )
    
    await callback.message.edit_text(
        text, 
        reply_markup=get_preflight_kb(
            data.get("pf_model"), 
            data.get("pf_ratio"), 
            data.get("pf_quality")
        ), 
        parse_mode="Markdown"
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
    
    # 1. Считываем АКТУАЛЬНЫЕ данные из состояния (меню)
    prompt = data.get("pf_prompt")
    image_urls = data.get("pf_image_urls")
    model_type = data.get("pf_model")
    ratio = data.get("pf_ratio")
    quality = data.get("pf_quality")
    
    cost = config.COST_PRO if model_type == "pro" else config.COST_STANDARD
    use_pro = (model_type == "pro")
    
    # Логика разрешения
    resolution = "1K"
    if use_pro:
        if quality == "4k": resolution = "4K"
        elif quality == "2k": resolution = "2K"
    
    # 2. Просто уведомляем пользователя (Toast), НЕ трогая сообщение с меню
    await callback.answer(f"🚀 Запускаю...", show_alert=False)
    
    # 3. Запускаем генерацию
    # Меню останется висеть в чате, и юзер сможет поменять настройки и нажать снова
    await process_generation(
        callback.message, 
        callback.from_user.id, 
        prompt, 
        image_urls, 
        aspect_ratio=ratio, 
        cost=cost, 
        use_pro_model=use_pro, 
        resolution=resolution,
        is_blend_mode=data.get("is_blend_mode", False)

    )

    # 🔥 Сбрасываем флаг после успешной генерации
    from_retry_flow = data.get("force_pro_mode", False)  # 👈 ПОЛУЧАЕМ ЗДЕСЬ
    if from_retry_flow:
        await state.update_data(force_pro_mode=False)
        
        # Логируем заказ из Retry Flow с РЕАЛЬНОЙ моделью
        from app.services.admin_logger import log_order_from_retry
        await log_order_from_retry(
            callback.bot,
            callback.from_user.id,
            cost,
            model_type  # 👈 ПЕРЕДАЁМ РЕАЛЬНУЮ МОДЕЛЬ
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
    
    # 🔥 ЕСЛИ ЭТО BROADCAST - ИСПОЛЬЗУЕМ СОХРАНЁННЫЙ ПРОМПТ 🔥
    if is_from_broadcast and broadcast_prompt:
        await state.update_data(
        pf_prompt=broadcast_prompt,
        pf_image_urls=image_urls,
        pf_ratio=broadcast_ratio,
        pf_model=broadcast_model,  # 👈 ИСПОЛЬЗУЕМ МОДЕЛЬ ИЗ ПОСТА
        pf_quality="2k",
        is_broadcast_gen=True  # 👈 ДОБАВЬ ФЛАГ
    )
        await state.set_state(GenState.preflight_check)
    
    # 🔥 УПРОЩЁННОЕ СООБЩЕНИЕ ДЛЯ BROADCAST 🔥
        text = (
        f"🎨 *Параметры генерации*\n\n"
        f"Выбери модель и жми \"Сгенерировать\"👇"
    )
        await message.answer(
        text,
        reply_markup=get_preflight_kb("standard", broadcast_ratio, "2k"),
        parse_mode="Markdown"
    )
        return
    # 🔥 КОНЕЦ BROADCAST ЛОГИКИ 🔥
    
    # Обычный флоу (без изменений)
    if count == 1:
        if full_caption:
        # 🚫 ДОБАВЬ ПРОВЕРКУ ЗДЕСЬ 👇
            if is_lazy_prompt(full_caption):
                await send_lazy_prompt_message(message)
                return
        # 👆 КОНЕЦ ВСТАВКИ
            await start_preflight_check(message, state, full_caption, image_urls)
        else:
            await state.update_data(pending_image_urls=image_urls)
            await state.set_state(GenState.waiting_for_caption)
            await message.reply(
                "📸 **Готово! Фото поймал.**\nНапиши, что с ним сделать?", 
                parse_mode="Markdown"
            )
    else:  # >= 2 фото
        await state.update_data(pending_image_urls=image_urls)
        if full_caption:
                    # 🚫 ДОБАВЬ ПРОВЕРКУ ЗДЕСЬ 👇
            if is_lazy_prompt(full_caption):
                await send_lazy_prompt_message(message)
                return
            await start_preflight_check(message, state, full_caption, image_urls)
        else:
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
    
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

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
    from app.handlers.start import send_main_menu
    await send_main_menu(callback.message, callback.from_user.id)

@router.message(StateFilter(GenState.preflight_check, GenState.selecting_ratio), F.text)
async def handle_new_prompt_during_settings(message: types.Message, state: FSMContext):
    """
    Если юзер был в меню настроек (или выбора формата), 
    но решил просто написать новый промпт — начинаем всё заново.
    """
    # 1. Проверяем, не нажал ли он кнопку меню (Старт, Профиль и т.д.)
    if message.text in IGNORED_TEXTS: 
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

@router.message(F.chat.type == "private", F.text, StateFilter(GenState.free_mode, None))
async def handle_free_text(message: types.Message, state: FSMContext):
    """Обработка текста без фото"""
    if message.text in IGNORED_TEXTS: 
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
        text = (
    f"🎨 *Параметры генерации*\n\n"
    f"Выбери модель и жми \"Сгенерировать\"👇"
)
        await message.answer(
    text,
    reply_markup=get_preflight_kb("standard", ratio, "2k"),
    parse_mode="Markdown"
)
        return
    
# Обычный флоу
    if message.caption:
        lazy_check = is_lazy_prompt(message.caption)  
        if lazy_check:
            await send_lazy_prompt_message(message)
            return

        # 🔥 ВОССТАНАВЛИВАЕМ force_pro_mode ЕСЛИ БЫЛ 👇
        if force_pro_mode:
            await state.update_data(force_pro_mode=True)
        # 👆 ВСТАВЬ ЭТО

        await start_preflight_check(message, state, message.caption, [url])
    else:
        # 🔥 ВОССТАНАВЛИВАЕМ force_pro_mode ЕСЛИ БЫЛ 👇
        if force_pro_mode:
            await state.update_data(force_pro_mode=True)
        # 👆 И ЭТО
        
        await state.update_data(pending_image_urls=[url])
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
            params.get("resolution", "1K"),
            is_blend_mode=params.get("is_blend_mode", False)

        )
    except Exception as e:
        print(f"❌ Ошибка reroll: {e}")
        await callback.answer("❌ Ошибка перегенерации", show_alert=True)

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
                # 🛡️ ДОБАВИЛИ ТАЙМАУТ: Если качает дольше 30 сек — обрываем, чтобы не вешать сервер
                timeout = aiohttp.ClientTimeout(total=30)
                
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    # ssl=False оставляем, это необходимость для этого провайдера
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
                # Если не вышло скачать (таймаут или ошибка), пробуем отправить ссылку как текст/файл
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
        except: 
            use_pro = False
        
        cost = config.COST_PRO if use_pro else config.COST_STANDARD
        
        await state.update_data(
            editing_file_id=history_item.file_id,
            edit_use_pro=use_pro,
            edit_cost=cost
        )
        await state.set_state(GenState.waiting_for_edit_instruction)
        
        await callback.message.reply(
            f"🎨 **Режим редактирования** ({cost}🍌)\nЧто изменить?", 
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
    
    await start_preflight_check(message, state, instruction, [img_url])

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
    "скрестить", "скрести", "составь",
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
    resolution: str = "1K",
    is_blend_mode: bool = False

):
    """Основная функция генерации изображений"""
    bot = message.bot 
    
    # 1. Проверка и списание баланса
    async with async_session() as session:
        has_balance = await check_and_deduct_balance(session, user_id, amount=cost)
        balance_left = await get_user_balance(session, user_id)

    if not has_balance:
        # 🔥 SMART ALERT: Определяем сценарий и показываем умное уведомление
        alert_text, alert_kb = await get_smart_alert_message(user_id, balance_left, cost)
        
        await message.answer(
            alert_text,
            reply_markup=alert_kb.as_markup(),
            parse_mode="HTML"
        )
        return

    # ✅ Нормализация URL
    final_urls = normalize_image_urls(image_urls)
    
    # 🔥 ОПРЕДЕЛЯЕМ СЦЕНАРИЙ: Простой vs Сложный
    is_complex_standard = (not use_pro_model and len(final_urls) >= 2)

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
            aspect_ratio, use_pro_model, None, resolution
        )
        
        # 5. Обработка результата
        result_file = None
        source_url = None
        
        if result_data and isinstance(result_data, tuple):
            result_file, source_url = result_data
        elif result_data: 
            result_file = result_data
        
        if result_file:
            # 🔥 УДАЛЯЕМ СООБЩЕНИЕ ТОЛЬКО ДЛЯ ПРОСТОГО СЦЕНАРИЯ
            if should_delete_wait_msg:
                try: 
                    await wait_msg.delete()
                except: 
                    pass
            
# 6. Формирование caption (НОВЫЙ ВАРИАНТ)
            caption = (
                f"🍌 <b>Готово!</b>\n"
                f"🔋 Осталось: <b>{balance_left}</b> 🍌\n\n"
                f"✨ Получилось круто? <b>Похвастайся результатом </b>в <a href='https://t.me/nanabanan_chat'>нашем чате</a>!\n"
                f"Авторов лучших работ награждаем бананами 🍌\n\n"
                f"Сгенерировано в @nan0banana_bot"
            )
            
            # 7. Сжатие для превью
            file_bytes = result_file.data
            compressed_bytes = smart_compress_image(file_bytes)
            preview_file = types.BufferedInputFile(compressed_bytes, filename="result.png")
            
            # 8. Отправка
            try:
                sent_msg = await message.answer_photo(
                    preview_file, 
                    caption=caption, 
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"⚠️ Ошибка отправки фото: {e}")
                sent_msg = await message.answer_document(
                    result_file, 
                    caption=caption, 
                    parse_mode="HTML"
                )

            # 9. Сохранение в БД
            sent_file_id = (
                sent_msg.photo[-1].file_id if sent_msg.photo 
                else sent_msg.document.file_id
            )

            await log_generation(
                bot, 
                message.chat, # ✅ Берем данные из ЧАТА (это всегда юзер)
                prompt=prompt, 
                model="PRO" if use_pro_model else "Standard", 
                photo_file_id=sent_file_id
            )
            
            meta_data = json.dumps({
                "prompt": prompt,
                "image_urls": final_urls,
                "ratio": aspect_ratio,
                "cost": cost,
                "pro": use_pro_model,
                "resolution": resolution,
                "is_blend_mode": is_blend_mode

            })
            
            async with async_session() as session:
                await add_history(
                    session, user_id, "user", prompt, 
                    has_image=bool(final_urls)
                )
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
                    reply_markup=get_result_kb(db_id, use_pro_model, cost)
                )
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
        
        # 3. 🛡️ ПЕРЕВОДЧИК ОШИБОК ДЛЯ ПОЛЬЗОВАТЕЛЯ
        err_msg = str(e).lower()

        if "500" in err_msg or "internal server" in err_msg:
            user_friendly_text = (
        "🔧 <b>Nano Banana Pro временно недоступен.</b>\n"
        "Обычно это решается за 1-2 минуты. Попробуйте ещё раз!"
    )
        
        # Сценарий А: NSFW / Цензура
        elif "sensitive" in err_msg or "nsfw" in err_msg or "safety" in err_msg:
            user_friendly_text = (
                "🔞 <b>Сработал фильтр контента!</b>\n"
                "Нейросеть посчитала запрос недопустимым (18+, насилие или запрещенные темы).\n"
                "Пожалуйста, измените формулировку запроса."
            )
        
        # Сценарий Б: Тайм-аут (долго думал)
        elif "timeout" in err_msg:
            user_friendly_text = (
                "🐢 <b>Время ожидания истекло.</b>\n"
                "Сервер перегружен сложными задачами (например, 2K + много лиц).\n"
                "Попробуйте позже или выберите качество Standard."
            )
            
        # Сценарий В: Перегрузка (Busy)
        elif "busy" in err_msg or "queue" in err_msg:
            user_friendly_text = (
                "🚦 <b>Высокая нагрузка.</b>\n"
                "Все графические процессоры заняты.\n"
                "Пожалуйста, повторите попытку через минуту."
            )

        # 🆕 Сценарий Д: Пустой ответ (Скрытый фильтр)
        elif "no image" in err_msg or "empty" in err_msg or "content found" in err_msg:
            user_friendly_text = (
                "🫥 <b>Нейросеть не выдала результат.</b>\n"
                "Обычно это происходит, если в генерации промелькнуло что-то запрещенное (Soft Filter).\n"
                "Пожалуйста, измените формулировку запроса."
            )

        # Сценарий Г: Остальные ошибки (был последним)
        else:
            user_friendly_text = f"⚠️ <b>Техническая ошибка:</b>\n<code>{str(e)[:100]}</code>"

        # 4. Отправляем красивое сообщение
        final_text = f"{user_friendly_text}\n\n💰 <b>{cost} 🍌 возвращены на баланс.</b>"

        try: 
            await wait_msg.edit_text(final_text, parse_mode="HTML")
        except: 
            await message.answer(final_text, parse_mode="HTML")

@router.callback_query(F.data.startswith("bc_"))
async def cb_broadcast_generate(callback: types.CallbackQuery, state: FSMContext):
    """Обработка нажатия кнопки генерации из рассылки"""
    
    broadcast_id = int(callback.data.split("_")[1])
    
    # Получаем промпт И ФОРМАТ из БД
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )
        broadcast = result.scalar_one_or_none()
    
    if not broadcast or not broadcast.hidden_prompt:
        await callback.answer("⚠️ Ошибка: промпт не найден", show_alert=True)
        return
    
    # Сохраняем промпт И ФОРМАТ в state
    await state.update_data(
        broadcast_prompt=broadcast.hidden_prompt,
        broadcast_ratio=broadcast.aspect_ratio or "1:1",
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