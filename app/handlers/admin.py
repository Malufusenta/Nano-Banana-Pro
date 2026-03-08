from aiogram import Router, types, F, Bot, html
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.config import ADMIN_IDS
from app.database import async_session
from app.services.user_service import find_user_by_input, admin_change_balance, get_user_admin_card_data
from app.services.payment_service import confirm_purchase
from app.handlers.start import get_main_kb
import asyncio  # 👈 Для фоновых задач
import json     # 👈 Для парсинга JSON (если ещё нет)
from sqlalchemy import select, func
from app.models import User, Purchase, Broadcast
from app import config
from app.services.analytics_service import (
    get_analytics_report, 
    format_report_message,
    get_payment_depth_stats,       # 👈 Новое
    format_payment_depth_message   # 👈 Новое
)
from datetime import datetime, timedelta, timezone
from aiogram.fsm.state import State, StatesGroup
import logging
logger = logging.getLogger(__name__)

class StatsState(StatesGroup):
    waiting_for_custom_dates = State()
class PaymentDepthState(StatesGroup):
    waiting_for_dates = State()

router = Router()


# --- СОСТОЯНИЯ АДМИНА ---
class AdminState(StatesGroup):
    waiting_for_user_search = State()
    waiting_for_balance_change = State()
    waiting_for_message = State()

# --- СОСТОЯНИЯ ДЛЯ РАССЫЛКИ ---
class BroadcastState(StatesGroup):
    waiting_for_content = State()       # Ждём контент (текст/фото/альбом)
    waiting_for_buttons = State()  
    waiting_for_model = State()       # 👈 УЖЕ ЕСТЬ — просто убедись
    waiting_for_aspect_ratio = State()  # 👈 ДОБАВЬ ЭТУ СТРОКУ
     # Ждём список кнопок
    waiting_for_confirmation = State()  # Показываем превью, ждём подтверждения

    # --- СОСТОЯНИЯ ДЛЯ СОЗДАНИЯ POST LINK ---
class PostLinkState(StatesGroup):
    waiting_for_prompt = State()
    waiting_for_model = State()
    waiting_for_aspect_ratio = State()

class PromptsState(StatesGroup):
    waiting_for_dates = State()


# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================
def get_admin_menu_kb():
    """Клавиатура главного меню админки"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🐳 Глубокая аналитика", callback_data="admin_payment_depth")
    builder.button(text="🔍 Найти пользователя", callback_data="admin_find_user")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast") 
    builder.button(text="📋 Рекламные сценарии", callback_data="admin_scenarios_menu")  # 👈 НОВАЯ КНОПКА
    builder.button(text="🔗 Создать ссылку", callback_data="admin_create_postlink")  # 👈 НОВАЯ КНОПКА
    builder.button(text="📈 Отчёты", callback_data="admin_stats_new")  # Новая
    builder.button(text="🎨 Промпты", callback_data="admin_prompts")  # ← НОВАЯ КНОПКА
    builder.button(text="💳 Баланс kie.ai", callback_data="admin_kie_balance")
    builder.button(text="❌ Выйти", callback_data="close_admin")
    builder.adjust(2, 2, 1, 2, 1, 1)  # По 2 в ряд для красоты
    return builder.as_markup()


def get_cancel_kb():
    """Кнопка отмены (возврат в админ-меню)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_menu")
    return builder.as_markup()


async def send_admin_menu(target: types.Message):
    """Отправляет меню админки"""
    await target.answer(
        "👑 **Панель Администратора**", 
        reply_markup=get_admin_menu_kb(), 
        parse_mode="Markdown"
    )


async def log_admin_action(admin_id: int, action: str, target_id: int = None):
    """Логирует действия админа для аудита"""
    logger.info(f"👑 ADMIN LOG: Admin {admin_id} | Action: {action} | Target: {target_id}")
    # Можно также сохранять в БД или отправлять в канал логов


# =====================================================================
# ГЛАВНОЕ МЕНЮ АДМИНА
# =====================================================================
@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Команда /admin - открывает панель администратора"""
    if message.from_user.id not in ADMIN_IDS:
        return

    await log_admin_action(message.from_user.id, "opened_admin_panel")
    
    await message.answer(
        "👑 **Панель Администратора**", 
        reply_markup=get_admin_menu_kb(), 
        parse_mode="Markdown"
    )


# =====================================================================
# СТАТИСТИКА
# =====================================================================
@router.callback_query(F.data == "admin_stats")
async def cb_stats(callback: types.CallbackQuery):
    """Показывает статистику с живым подсчетом кассы"""
    async with async_session() as session:
        # 1. Считаем пользователей (Count ID)
        result = await session.execute(select(func.count(User.id)))
        users_count = result.scalar()
        
        # 2. Считаем генерации (Сумма total_generations_used)
        # (используем try, вдруг колонка пустая)
        try:
            res_gens = await session.execute(select(func.sum(User.total_generations_used)))
            gens_count = res_gens.scalar() or 0
        except:
            gens_count = 0

# 3. 🔥 СЧИТАЕМ КАССУ (Только успешные!)
        try:
            # Складываем price, ГДЕ status == 'succeeded'
            res_money = await session.execute(
                select(func.sum(Purchase.price)).where(Purchase.status == "succeeded")
            )
            money_total = res_money.scalar() or 0
        except:
            money_total = 0

    text = (
        "📊 **Статистика Бота**\n\n"
        f"👥 Людей: **{users_count}**\n"
        f"🎨 Генераций: **{gens_count}**\n"
        f"💰 Касса: **{money_total}₽**"
    )
    
    builder = InlineKeyboardBuilder()
    # Оставляем твою кнопку возврата
    builder.button(text="🔙 Меню", callback_data="admin_menu")
    
    await callback.message.edit_text(
        text, 
        reply_markup=builder.as_markup(), 
        parse_mode="Markdown"
    )
    await callback.answer()


# =====================================================================
# ВОЗВРАТ В МЕНЮ
# =====================================================================
@router.callback_query(F.data == "admin_menu")
async def cb_back_admin(callback: types.CallbackQuery, state: FSMContext):
    """Возврат в главное меню админки"""
    await state.clear()
    
    await callback.message.edit_text(
        "👑 **Панель Администратора**", 
        reply_markup=get_admin_menu_kb(), 
        parse_mode="Markdown"
    )
    await callback.answer()

# =====================================================================
# РАССЫЛКА
# =====================================================================
@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast_menu(callback: types.CallbackQuery):
    """Меню рассылок"""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать рассылку", callback_data="broadcast_create")
    builder.button(text="📋 История рассылок", callback_data="broadcast_history")
    builder.button(text="🔙 Меню", callback_data="admin_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(
        "📢 <b>Управление рассылками</b>\n\n"
        "Выберите действие:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "broadcast_history")
async def cb_broadcast_history(callback: types.CallbackQuery):
    """История рассылок"""
    async with async_session() as session:
        result = await session.execute(
            select(Broadcast)
            .order_by(Broadcast.created_at.desc())
            .limit(10)
        )
        broadcasts = result.scalars().all()
    
    if not broadcasts:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="admin_broadcast")
        await callback.message.edit_text(
            "📋 <b>История рассылок</b>\n\nРассылок пока не было.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    text = "📋 <b>История рассылок (последние 10)</b>\n\n"
    
    for bc in broadcasts:
        # Статус
        status_icon = {"draft": "📝", "sending": "⏳", "completed": "✅"}.get(bc.status, "❓")
        
        # Дата
        date_str = bc.created_at.strftime("%d.%m.%Y %H:%M")
        
        # Тип контента
        content_type = {"photo": "🖼", "video": "🎥"}.get(bc.media_type, "📝")
        
        # Промпт
        prompt_str = f"\n└─ 🎨 Промпт: <code>{bc.hidden_prompt[:40]}...</code>" if bc.hidden_prompt else ""
        model_str = f" | {bc.model_type.upper()}" if bc.hidden_prompt and bc.model_type else ""
        
        text += (
            f"{status_icon} <b>#{bc.id}</b> {content_type} — {date_str}\n"
            f"├─ 👥 Отправлено: {bc.delivered_count}/{bc.sent_count}\n"
            f"├─ 🚫 Заблокировали: {bc.blocked_count}\n"
            f"└─ 📐 {bc.aspect_ratio or '1:1'}{model_str}"
            f"{prompt_str}\n\n"
        )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin_broadcast")
    
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "broadcast_create")
async def cb_broadcast_create(callback: types.CallbackQuery, state: FSMContext):
    """Начало создания рассылки - запрос контента"""
    await state.set_state(BroadcastState.waiting_for_content)
    
    await callback.message.answer(
        "📝 <b>Шаг 1/3: Контент рассылки</b>\n\n"
        "Отправьте сообщение, которое получат пользователи:\n"
        "• Текст\n"
        "• Фото с подписью\n"
        "• Альбом (2-10 фото)\n\n"
        "💡 Используйте HTML форматирование:\n"
        "<code>&lt;b&gt;жирный&lt;/b&gt;</code>\n"
        "<code>&lt;i&gt;курсив&lt;/i&gt;</code>\n"
        "<code>&lt;code&gt;моноширинный&lt;/code&gt;</code>",
        reply_markup=get_cancel_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BroadcastState.waiting_for_content)
async def process_broadcast_content(message: types.Message, state: FSMContext):
    """Обработка контента рассылки"""
    
    # Определяем тип контента
    media_type = None
    media_file_ids = []
    message_text = None
    
    # 1. Текст
    if message.text:
        media_type = None
        message_text = message.text
    
    # 2. Фото (одно)
    elif message.photo:
        media_type = "photo"
        media_file_ids.append(message.photo[-1].file_id)
        message_text = message.caption or ""


    # 3. Видео (одно) ✅ НОВОЕ
    elif message.video:
        media_type = "video"
        media_file_ids.append(message.video.file_id)
        message_text = message.caption or ""
    
    # 3. Альбом (несколько фото)
    elif message.media_group_id:
        # Для альбомов нужна отдельная логика (сложнее)
        # Пока просто говорим что не поддерживается
        await message.answer(
            "⚠️ Альбомы пока не поддерживаются.\n"
            "Отправьте одно фото или текст.",
            reply_markup=get_cancel_kb()
        )
        return
    
    else:
        await message.answer(
            "❌ Неподдерживаемый тип контента.\n"
            "Отправьте текст или фото.",
            reply_markup=get_cancel_kb()
        )
        return
    
    # Сохраняем в state
    await state.update_data(
        media_type=media_type,
        media_file_ids=media_file_ids,
        message_text=message_text
    )
    
    # Переходим к следующему шагу - кнопки
    await state.set_state(BroadcastState.waiting_for_buttons)
    
    await message.answer(
        "✅ Контент сохранён!\n\n"
        "📝 <b>Шаг 2/3: Кнопки</b>\n\n"
        "Отправьте кнопки в формате (каждая с новой строки):\n\n"
        "<b>Для URL-кнопки:</b>\n"
        "<code>Текст кнопки | https://example.com</code>\n\n"
        "<b>Для кнопки-генерации:</b>\n"
        "<code>Текст кнопки | %промпт для генерации%</code>\n\n"
        "<b>Пример:</b>\n"
        "<code>🔥 Попробовать | %luxury room, 4k, realistic%</code>\n"
        "<code>📢 Наш канал | https://t.me/yourchannel</code>\n\n"
        "Или отправьте <code>-</code> чтобы пропустить (без кнопок)",
        reply_markup=get_cancel_kb(),
        parse_mode="HTML"
    )

@router.message(BroadcastState.waiting_for_buttons)
async def process_broadcast_buttons(message: types.Message, state: FSMContext):
    """Обработка кнопок рассылки"""
    
    # Если админ пропускает кнопки
    if message.text and message.text.strip() == "-":
        await state.update_data(buttons=None, hidden_prompt=None)
        await show_broadcast_preview(message, state)
        return
    
    # 🔥 НОВЫЙ ПАРСЕР - ПОДДЕРЖКА МНОГОСТРОЧНЫХ ПРОМПТОВ 🔥
    buttons = []
    hidden_prompt = None
    
    raw_text = message.text.strip()
    
    # Разбиваем на блоки по символу |
    # Каждый блок = одна кнопка
    button_blocks = []
    current_block = ""
    
    for line in raw_text.split('\n'):
        if '|' in line and current_block and '|' in current_block:
            # Новая кнопка началась
            button_blocks.append(current_block.strip())
            current_block = line
        else:
            # Продолжение текущей кнопки
            current_block += " " + line if current_block else line
    
    # Добавляем последний блок
    if current_block:
        button_blocks.append(current_block.strip())
    
    # Парсим каждый блок
    for block in button_blocks:
        if not block or '|' not in block:
            continue
        
        parts = block.split('|', 1)
        text = parts[0].strip()
        data = parts[1].strip()
        
        # Определяем тип кнопки
        if data.startswith('%') and data.endswith('%'):
            # Type B: Callback Action (генерация)
            prompt = data[1:-1].strip()  # Убираем %
            
            if not prompt:
                await message.answer(
                    "❌ Пустой промпт в кнопке!\n"
                    "Попробуйте снова:",
                    reply_markup=get_cancel_kb()
                )
                return
            
            hidden_prompt = prompt
            
            buttons.append({
                "text": text,
                "type": "callback",
                "data": "broadcast_generate"
            })
        
        elif data.startswith('http://') or data.startswith('https://'):
            # Type A: URL
            buttons.append({
                "text": text,
                "type": "url",
                "data": data
            })
        
        else:
            await message.answer(
                f"❌ Неверный формат данных: <code>{data[:50]}...</code>\n\n"
                "URL должен начинаться с http:// или https://\n"
                "Промпт должен быть в %промпт%\n\n"
                "Попробуйте снова:",
                reply_markup=get_cancel_kb(),
                parse_mode="HTML"
            )
            return
    
    if not buttons:
        await message.answer(
            "❌ Не найдено ни одной кнопки!\n"
            "Отправьте <code>-</code> чтобы пропустить, или добавьте кнопки.\n\n"
            "Попробуйте снова:",
            reply_markup=get_cancel_kb(),
            parse_mode="HTML"
        )
        return
    
    # Сохраняем кнопки
    import json
    await state.update_data(
        buttons=json.dumps(buttons, ensure_ascii=False),
        hidden_prompt=hidden_prompt
    )
    
# 🔥 НОВЫЙ БЛОК - ЕСЛИ ЕСТЬ ПРОМПТ, СПРАШИВАЕМ МОДЕЛЬ, ПОТОМ ФОРМАТ 🔥
    if hidden_prompt:
        await state.set_state(BroadcastState.waiting_for_model)
        
        builder = InlineKeyboardBuilder()
        builder.button(text=f"🍌 Standard ({config.COST_STANDARD} банан)", callback_data="bc_model_standard")
        builder.button(text=f"🍌 Nano Banana 2 ({config.COST_NB2_1K} банана)", callback_data="bc_model_nb2")
        builder.button(text=f"💎 PRO ({config.COST_PRO_1K} банана)", callback_data="bc_model_pro")
        builder.button(text="❌ Отмена", callback_data="admin_menu")
        builder.adjust(1)
        
        await message.answer(
            "🤖 <b>Выберите модель генерации:</b>\n\n"
            "Это модель, которая будет генерировать изображение по кнопке в рассылке.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        return
    # 🔥 КОНЕЦ НОВОГО БЛОКА 🔥

    # Показываем превью
    await show_broadcast_preview(message, state)

async def show_broadcast_preview(message: types.Message, state: FSMContext):
    """Показывает превью рассылки и запрашивает подтверждение"""
    
    data = await state.get_data()
    
    # Переходим в состояние ожидания подтверждения
    await state.set_state(BroadcastState.waiting_for_confirmation)
    
    # Формируем превью
    media_type = data.get('media_type')
    message_text = data.get('message_text', '')
    buttons_json = data.get('buttons')
    
    # Строим клавиатуру для превью
    preview_kb = InlineKeyboardBuilder()
    
    if buttons_json:
        import json
        buttons = json.loads(buttons_json)
        
        for btn in buttons:
            if btn['type'] == 'url':
                preview_kb.button(text=btn['text'], url=btn['data'])
            else:
                preview_kb.button(text=btn['text'], callback_data="preview_only")
        
        preview_kb.adjust(1)  # По одной кнопке в ряд
    
    # Кнопки управления
    control_kb = InlineKeyboardBuilder()
    control_kb.button(text="✅ Начать рассылку", callback_data="broadcast_confirm")
    control_kb.button(text="❌ Отменить", callback_data="admin_menu")
    control_kb.adjust(1)
    
    # Отправляем превью
    await message.answer(
        "📋 <b>Шаг 3/3: Превью рассылки</b>\n\n"
        "Так увидят пользователи:",
        parse_mode="HTML"
    )
    
    # Отправляем контент как будет выглядеть у юзера
    if media_type == "photo":
        file_ids = data.get('media_file_ids', [])
        await message.answer_photo(
            photo=file_ids[0],
            caption=message_text,
            reply_markup=preview_kb.as_markup() if buttons_json else None,
            parse_mode="HTML"
        )

    elif media_type == "video":  # ✅ НОВОЕ
        file_ids = data.get('media_file_ids', [])
        await message.answer_video(
            video=file_ids[0],
            caption=message_text,
            reply_markup=preview_kb.as_markup() if buttons_json else None,
            parse_mode="HTML"
        )

    else:
        await message.answer(
            message_text,
            reply_markup=preview_kb.as_markup() if buttons_json else None,
            parse_mode="HTML"
        )
    
    # Кнопки подтверждения
    await message.answer(
        "Начать рассылку?",
        reply_markup=control_kb.as_markup()
    )        

@router.callback_query(F.data == "broadcast_confirm")
async def cb_broadcast_confirm(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждение и запуск рассылки"""
    
    data = await state.get_data()
    
    # Сохраняем рассылку в БД
    async with async_session() as session:
        # Считаем активных пользователей
        from sqlalchemy import select, func
        result = await session.execute(
            select(func.count(User.id))
        )
        total_users = result.scalar() or 0
        
        # Создаём запись о рассылке
        broadcast = Broadcast(
            admin_id=callback.from_user.id,
            message_text=data.get('message_text'),
            media_type=data.get('media_type'),
            media_file_ids=json.dumps(data.get('media_file_ids', [])),  # 👈 ПРАВИЛЬНО
            buttons=data.get('buttons'),
            hidden_prompt=data.get('hidden_prompt'),
            aspect_ratio=data.get('aspect_ratio', '1:1'),
            model_type=data.get('model_type', 'standard'),  # 👈 ДОБАВИТЬ
            status="sending",
            total_users=total_users
        )
        
        session.add(broadcast)
        await session.commit()
        await session.refresh(broadcast)
        
        broadcast_id = broadcast.id
    
    await callback.message.edit_text(
        f"✅ <b>Рассылка #{broadcast_id} запущена!</b>\n\n"
        f"👥 Отправляется {total_users} пользователям...\n"
        f"⏳ Это займёт примерно {total_users // 25 // 60 + 1} минут.\n\n"
        f"Вы получите отчёт после завершения.",
        parse_mode="HTML"
    )
    await callback.answer()
    
    await state.clear()
    
    # Запускаем рассылку в фоне
    from app.services.broadcaster import start_broadcast
    asyncio.create_task(
        start_broadcast(callback.bot, broadcast_id, callback.from_user.id)
    )

# =====================================================================
# ПОИСК ПОЛЬЗОВАТЕЛЯ
# =====================================================================
@router.callback_query(F.data == "admin_find_user")
async def cb_find_user(callback: types.CallbackQuery, state: FSMContext):
    """Запускает процесс поиска пользователя"""
    await state.set_state(AdminState.waiting_for_user_search)
    
    await callback.message.answer(
        "🔍 **Введите ID пользователя или @username:**",
        reply_markup=get_cancel_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminState.waiting_for_user_search)
async def process_find_user(message: types.Message, state: FSMContext):
    """Обработка поиска пользователя"""
    user_input = message.text.strip()
    
    async with async_session() as session:
        # Сначала ищем пользователя по ID или username
        user = await find_user_by_input(session, user_input)
        
        if not user:
            await message.answer(
                "❌ Пользователь не найден.\n"
                "Попробуй еще раз или жми /admin",
                reply_markup=get_cancel_kb()
            )
            return
        
        # Теперь получаем полную статистику по найденному user_id
        user_data = await get_user_admin_card_data(session, user.telegram_id)

    await log_admin_action(message.from_user.id, "found_user", user_data['user'].telegram_id)
    
    await state.clear()
    await show_user_card(message, user_data)

async def show_user_card(message: types.Message, user_data: dict):
    """Показывает расширенную карточку пользователя"""
    user = user_data['user']
    
    safe_name = html.quote(str(user.full_name))  # ← quote вместо escape
    safe_username = html.quote(str(user.username)) if user.username else "Нет"
    
    # Формируем источник трафика
    source_text = user_data['source']
    if user_data['referrer_id']:
        source_text = f"Реферал от ID: {user_data['referrer_id']}"
    
    # Бонусы
    bonuses = []
    if user_data['channel_bonus_claimed']:
        bonuses.append("📢 Канал")
    if user_data['chat_bonus_claimed']:
        bonuses.append("💬 Чат")
    bonus_text = ", ".join(bonuses) if bonuses else "Не получал"
    
    # Форматируем дату регистрации
    reg_date = user.created_at.strftime("%d.%m.%Y")
    days_text = f"{user_data['days_with_us']} дней" if user_data['days_with_us'] != 1 else "1 день"
    
    # Форматируем последнюю оплату
    last_payment_text = "Нет платежей"
    if user_data['last_payment_date']:
        from datetime import datetime, timezone
        delta = (datetime.now(timezone.utc) - user_data['last_payment_date'].replace(tzinfo=timezone.utc)).days
        if delta == 0:
            last_payment_text = "Сегодня"
        elif delta == 1:
            last_payment_text = "Вчера"
        else:
            last_payment_text = user_data['last_payment_date'].strftime("%d.%m.%Y")
    
    # Форматируем последнюю генерацию
    last_gen_text = "Не генерировал"
    if user_data['last_generation_at']:
        from datetime import datetime, timezone
        delta = (datetime.now(timezone.utc) - user_data['last_generation_at'].replace(tzinfo=timezone.utc)).days
        if delta == 0:
            last_gen_text = user_data['last_generation_at'].strftime("Сегодня %H:%M")
        elif delta == 1:
            last_gen_text = "Вчера"
        else:
            last_gen_text = user_data['last_generation_at'].strftime("%d.%m.%Y")
    
    total_balance = user_data['balance_free'] + user_data['balance_paid']
    block_status = "🔴 Заблокирован" if user.is_blocked else "🟢 Активен"

    
    text = (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"🆔 <code>{user.telegram_id}</code>\n"
        f"👤 Имя: {safe_name}\n"
        f"🔗 Ник: @{safe_username}\n"
        f"📅 Дата регистрации: {reg_date} ({days_text} с нами)\n"
        f"📊 Статус: {user_data['status']}\n\n"
        f"🔒 Доступ: {block_status}\n\n"  # 👈 ДОБАВИЛИ

        
        f"💎 <b>Баланс: {total_balance} 🍌</b>\n"
        f"├─ Платные: {user_data['balance_paid']} 🍌\n"
        f"└─ Бесплатные: {user_data['balance_free']} 🍌\n\n"
        
        f"🎨 Генераций сделано: <b>{user_data['total_generations']}</b>\n"
        f"📆 Дата последней генерации: {last_gen_text}\n\n"
        
        f"💰 <b>Платежи:</b>\n"
        f"  • Количество: {user_data['payments_count']}\n"
        f"  • Сумма: {user_data['payments_sum']}₽\n"
        f"  • Последняя оплата: {last_payment_text}\n\n"
        
        f"🔗 <b>Источник:</b> {source_text}\n"
        f"👥 <b>Рефералов:</b> {user_data['referrals_count']}\n"
        f"🎁 <b>Бонусы:</b> {bonus_text}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить", callback_data=f"adm_add_{user.telegram_id}")
    builder.button(text="➖ Отнять", callback_data=f"adm_rem_{user.telegram_id}")
    # 👇 НОВАЯ КНОПКА
    block_text = "🔓 Разблокировать" if user.is_blocked else "🔒 Заблокировать"
    builder.button(text=block_text, callback_data=f"adm_block_{user.telegram_id}")
    builder.button(text="✉️ Написать", callback_data=f"adm_msg_{user.telegram_id}")
    builder.button(text="🔙 Меню", callback_data="admin_menu")
    builder.adjust(2, 1, 1, 1)  # 👈 Изменили разметку (добавили строку)

    
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# =====================================================================
# УПРАВЛЕНИЕ БАЛАНСОМ
# =====================================================================
@router.callback_query(F.data.startswith("adm_add_") | F.data.startswith("adm_rem_"))
async def cb_change_balance(callback: types.CallbackQuery, state: FSMContext):
    """Начало процесса изменения баланса"""
    parts = callback.data.split("_")
    action = parts[1]  # "add" или "rem"
    user_id = int(parts[2])
    
    await state.update_data(target_user_id=user_id, action_type=action)
    await state.set_state(AdminState.waiting_for_balance_change)
    
    op_text = "начислить" if action == "add" else "списать"
    
    await callback.message.answer(
        f"🔢 **Введи число, сколько бананов {op_text}:**",
        reply_markup=get_cancel_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminState.waiting_for_balance_change)
async def process_balance_change(message: types.Message, state: FSMContext, bot: Bot):
    """Обработка изменения баланса"""
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            await message.answer(
                "❌ Число должно быть положительным!",
                reply_markup=get_cancel_kb()
            )
            return
    except ValueError:
        await message.answer(
            "❌ Введи целое число!",
            reply_markup=get_cancel_kb()
        )
        return

    data = await state.get_data()
    target_id = data['target_user_id']
    action = data['action_type']
    
    # Если action == "rem", делаем число отрицательным
    final_amount = amount if action == "add" else -amount
    
    async with async_session() as session:
        new_balance = await admin_change_balance(session, target_id, final_amount)
    
    if new_balance is not None:
        await log_admin_action(
            message.from_user.id, 
            f"balance_change: {'+' if final_amount > 0 else ''}{final_amount}", 
            target_id
        )
        
        await message.answer(
            f"✅ **Баланс изменен!**\n"
            f"Новый баланс: **{new_balance} 🍌**",
            parse_mode="Markdown"
        )
        
        # Уведомляем юзера (только при начислении)
        if action == "add":
            try:
                await bot.send_message(
                    target_id, 
                    f"🎁 **Администратор начислил вам {amount} бананов!**\n"
                    f"Приятного творчества! 🍌",
                    parse_mode="Markdown"
                )
            except Exception as e:
                error_msg = str(e).lower()
                if "blocked" in error_msg:
                    await message.answer("⚠️ Пользователь заблокировал бота (уведомление не отправлено).")
                elif "not found" in error_msg:
                    await message.answer("⚠️ Пользователь удалил аккаунт (уведомление не отправлено).")
                else:
                    await message.answer(f"⚠️ Не удалось отправить уведомление: {str(e)[:100]}")
    else:
        await message.answer("❌ Ошибка базы данных.")
    
    await state.clear()
    await send_admin_menu(message)

@router.callback_query(F.data.startswith("adm_block_"))
async def cb_toggle_block(callback: types.CallbackQuery, bot: Bot):
    """Блокировка/разблокировка пользователя"""
    user_id = int(callback.data.split("_")[2])
    
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        # Переключаем статус блокировки
        user.is_blocked = not user.is_blocked
        new_status = user.is_blocked
        
        await session.commit()
    
    # Логируем действие в консоль
    action = "blocked" if new_status else "unblocked"
    await log_admin_action(callback.from_user.id, action, user_id)
    
    # 🔥 ЛОГИРУЕМ В КАНАЛ
    from app.services.admin_logger import log_user_block
    await log_user_block(
        bot=bot,
        admin_id=callback.from_user.id,
        admin_username=callback.from_user.username,
        user_id=user_id,
        user_name=user.full_name or "Неизвестно",
        user_username=user.username,
        is_blocked=new_status
    )
    
    # Уведомляем текущего админа
    status_text = "🔴 заблокирован" if new_status else "🟢 разблокирован"
    await callback.answer(f"✅ Пользователь {status_text}", show_alert=True)
    
    # Обновляем карточку пользователя
    async with async_session() as session:
        user_data = await get_user_admin_card_data(session, user_id)
    
    await show_user_card(callback.message, user_data)

# =====================================================================
# ОТПРАВКА СООБЩЕНИЯ (Support)
# =====================================================================
@router.callback_query(F.data.startswith("adm_msg_"))
async def cb_send_msg(callback: types.CallbackQuery, state: FSMContext):
    """Начало процесса отправки сообщения пользователю"""
    user_id = int(callback.data.split("_")[2])
    
    await state.update_data(target_user_id=user_id)
    await state.set_state(AdminState.waiting_for_message)
    
    await callback.message.answer(
        "✍️ **Введите текст сообщения для пользователя:**",
        reply_markup=get_cancel_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminState.waiting_for_message)
async def process_send_msg(message: types.Message, state: FSMContext, bot: Bot):
    """Отправка сообщения пользователю"""
    data = await state.get_data()
    target_id = data['target_user_id']
    
    try:
        await bot.send_message(
            chat_id=target_id, 
            text=f"📨 **Сообщение от поддержки:**\n\n{message.text}", 
            parse_mode="Markdown"
        )
        
        await log_admin_action(message.from_user.id, "sent_message", target_id)
        await message.answer("✅ **Сообщение отправлено!**", parse_mode="Markdown")
        
    except Exception as e:
        error_msg = str(e).lower()
        if "blocked" in error_msg:
            await message.answer("⚠️ **Пользователь заблокировал бота.**", parse_mode="Markdown")
        elif "not found" in error_msg:
            await message.answer("⚠️ **Пользователь удалил аккаунт.**", parse_mode="Markdown")
        elif "chat not found" in error_msg:
            await message.answer("⚠️ **Чат не найден.**", parse_mode="Markdown")
        else:
            await message.answer(f"❌ **Ошибка отправки:**\n`{str(e)[:100]}`", parse_mode="Markdown")
    
    await state.clear()
    await send_admin_menu(message)


# =====================================================================
# ПОДТВЕРЖДЕНИЕ ПЛАТЕЖА (Старая команда)
# =====================================================================
@router.message(Command("confirm_pay"))
async def cmd_confirm_pay(message: types.Message):
    """Команда для ручного подтверждения платежа"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        order_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.answer(
            "❌ **Неверный формат!**\n"
            "Используй: `/confirm_pay 123`",
            parse_mode="Markdown"
        )
        return

    async with async_session() as session:
        success = await confirm_purchase(session, order_id)
    
    if success:
        await log_admin_action(message.from_user.id, f"confirmed_payment: {order_id}")
        await message.answer(f"✅ **Заказ #{order_id} подтверждён.**", parse_mode="Markdown")
    else:
        await message.answer(f"❌ **Ошибка подтверждения заказа #{order_id}.**", parse_mode="Markdown")


# =====================================================================
# ВЫХОД ИЗ АДМИНКИ
# =====================================================================
@router.callback_query(F.data == "close_admin")
async def cb_exit_admin(callback: types.CallbackQuery, state: FSMContext):
    """Выход из админки в режим пользователя"""
    await state.clear()
    await log_admin_action(callback.from_user.id, "exited_admin_panel")
    
    # Удаляем сообщение с админ-кнопками
    try:
        await callback.message.delete()
    except: 
        pass
    
    # Отправляем обычное меню пользователя
    await callback.message.answer(
        "🏠 **Вы вернулись в главное меню.**", 
        reply_markup=get_main_kb(),
        parse_mode="Markdown"
    )
    await callback.answer("👋 Вышли из админки")

@router.callback_query(BroadcastState.waiting_for_aspect_ratio, F.data.startswith("bc_ratio_"))
async def cb_broadcast_select_ratio(callback: types.CallbackQuery, state: FSMContext):
    """Выбор формата для broadcast генерации"""
    ratio = callback.data.split("_")[2]
    
    await state.update_data(aspect_ratio=ratio)
    
    await callback.answer(f"✅ Формат: {ratio}")
    
    # Показываем превью
    await show_broadcast_preview(callback.message, state)

@router.callback_query(BroadcastState.waiting_for_model, F.data.startswith("bc_model_"))
async def cb_broadcast_select_model(callback: types.CallbackQuery, state: FSMContext):
    """Выбор модели для broadcast генерации"""
    model = callback.data.split("_")[2]  # standard / nb2 / pro
    
    await state.update_data(model_type=model)
    await state.set_state(BroadcastState.waiting_for_aspect_ratio)
    
    # Разные форматы для nb2
    builder = InlineKeyboardBuilder()
    if model == "nb2":
        ratios = ["1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4", "8:1", "9:16", "16:9", "21:9"]
    else:
        ratios = ["1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "21:9"]
    
    for r in ratios:
        builder.button(text=r, callback_data=f"bc_ratio_{r}")
    builder.adjust(3, 3, 2, 2)
    
    await callback.message.edit_text(
        "📐 <b>Выберите формат результата:</b>\n\n"
        "Это формат, в котором юзер получит изображение после генерации.\n"
        "💡 Выбирайте тот же формат, что на примере в рассылке!",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer(f"✅ Модель: {model}")

# СОЗДАНИЕ POST LINK
# =====================================================================
@router.callback_query(F.data == "admin_create_postlink")
async def cb_create_postlink_start(callback: types.CallbackQuery, state: FSMContext):
    """Начало создания Post Link"""
    await state.set_state(PostLinkState.waiting_for_prompt)
    
    await callback.message.answer(
        "🔗 <b>Создание ссылки для поста</b>\n\n"
        "📝 <b>Шаг 1/3: Промт</b>\n\n"
        "Отправьте промт для генерации (текст).\n"
        "Это то, что будет применяться к фото пользователя.\n\n"
        "<i>Пример: luxury bedroom, 4k, photorealistic</i>",
        reply_markup=get_cancel_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(PostLinkState.waiting_for_prompt)
async def process_postlink_prompt(message: types.Message, state: FSMContext):
    """Обработка промта"""
    prompt = message.text.strip()
    
    if len(prompt) < 5:
        await message.answer(
            "❌ Промт слишком короткий (минимум 5 символов).\n"
            "Попробуйте ещё раз:",
            reply_markup=get_cancel_kb()
        )
        return
    
    await state.update_data(postlink_prompt=prompt)
    await state.set_state(PostLinkState.waiting_for_model)
    
    # Кнопки выбора модели
    builder = InlineKeyboardBuilder()
# Кнопки выбора модели
    builder = InlineKeyboardBuilder()
    builder.button(text=f"🍌 Standard ({config.COST_STANDARD} банан)", callback_data="postlink_model_standard")
    builder.button(text=f"🍌 Nano Banana 2 ({config.COST_NB2_1K} банана)", callback_data="postlink_model_nb2")
    builder.button(text=f"💎 PRO ({config.COST_PRO_1K} банана)", callback_data="postlink_model_pro")
    builder.button(text="❌ Отмена", callback_data="admin_menu")
    builder.adjust(1)
    
    await message.answer(
        "✅ Промт сохранён!\n\n"
        "📝 <b>Шаг 2/3: Модель</b>\n\n"
        "Выберите модель для генерации:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(PostLinkState.waiting_for_model, F.data.startswith("postlink_model_"))
async def process_postlink_model(callback: types.CallbackQuery, state: FSMContext):
    """Выбор модели"""
    model = callback.data.split("_")[2]  # standard или pro
    
    await state.update_data(postlink_model=model)
    await state.set_state(PostLinkState.waiting_for_aspect_ratio)
    
    # Кнопки выбора формата
    builder = InlineKeyboardBuilder()
    data = await state.get_data()
    postlink_model = data.get("postlink_model", "standard")
    
    if postlink_model == "nb2":
        ratios = ["1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4", "8:1", "9:16", "16:9", "21:9"]
    else:
        ratios = ["1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "21:9"]
    
    for r in ratios:
        builder.button(text=r, callback_data=f"postlink_ratio_{r}")
    builder.button(text="❌ Отмена", callback_data="admin_menu")
    
    if postlink_model == "nb2":
        builder.adjust(3, 3, 2, 2, 4, 1)
    else:
        builder.adjust(3, 3, 2, 2, 1)
    
    await callback.message.edit_text(
        "✅ Модель сохранена!\n\n"
        "📝 <b>Шаг 3/3: Формат</b>\n\n"
        "Выберите соотношение сторон:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(PostLinkState.waiting_for_aspect_ratio, F.data.startswith("postlink_ratio_"))
async def process_postlink_finish(callback: types.CallbackQuery, state: FSMContext):
    """Завершение создания - генерация ссылки"""
    ratio = callback.data.split("_")[2]
    
    data = await state.get_data()
    prompt = data['postlink_prompt']
    model = data['postlink_model']
    
    # Импортируем модель
    from app.models import PostConfig
    from sqlalchemy import select, func
    
    # Генерируем ID (берем последний + 1)
    async with async_session() as session:
        result = await session.execute(
            select(func.max(PostConfig.id))
        )
        last_id = result.scalar() or 0
        new_id = last_id + 1
        
        config_id = f"post_{new_id}"
        
        # Сохраняем в БД
        new_config = PostConfig(
            config_id=config_id,
            prompt=prompt,
            model_type=model,
            aspect_ratio=ratio,
            created_by=callback.from_user.id
        )
        
        session.add(new_config)
        await session.commit()
    
    # Генерируем ссылку
    bot_username = (await callback.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={config_id}"
    
    # Формируем красивое сообщение
    text = (
        f"✅ <b>Ссылка создана!</b>\n\n"
        f"🔗 <code>{link}</code>\n\n"
        f"📋 <b>Настройки:</b>\n"
        f"• ID: <code>{config_id}</code>\n"
        f"• Промт: <code>{prompt[:80]}{'...' if len(prompt) > 80 else ''}</code>\n"
        f"• Модель: {model.upper()}\n"
        f"• Формат: {ratio}\n\n"
        f"💡 <b>Как использовать:</b>\n"
        f"1. Скопируйте ссылку выше\n"
        f"2. Создайте пост в канале с примером\n"
        f"3. Добавьте кнопку с этой ссылкой\n"
        f"4. Пользователи получат настроенный промт автоматически!"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer("🎉 Ссылка готова!")
    
    await state.clear()

@router.callback_query(F.data == "admin_stats_new")
async def cb_admin_stats(callback: types.CallbackQuery):
    """Главное меню статистики"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 За сегодня", callback_data="stats_today")
    builder.button(text="📅 За вчера", callback_data="stats_yesterday")
    builder.button(text="📅 За неделю (7 дней)", callback_data="stats_week")
    builder.button(text="📅 За месяц (30 дней)", callback_data="stats_month")
    builder.button(text="📅 За всё время", callback_data="stats_alltime")
    builder.button(text="🔧 Свой период", callback_data="stats_custom")  # 👈 ДОБАВЬ ЭТУ СТРОКУ
    builder.button(text="🔙 Назад", callback_data="admin_menu" \
    "")
    builder.adjust(1)
    
    await callback.message.edit_text(
        "📊 <b>Статистика</b>\n\nВыберите период:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.regexp(r"^stats_(today|yesterday|week|month|alltime|custom)$"))
async def cb_stats_period(callback: types.CallbackQuery, state: FSMContext):
    """Показывает статистику за выбранный период"""
    period = callback.data.split("_")[1]
    # Если это кастомный период - перенаправляем в отдельный handler
    if period == "custom":
        await cb_stats_custom_start(callback, state)
        return
    now = datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)
    today_start = get_today_start_msk()

# Определяем период
    if period == "today":
        date_from = today_start
        date_to = now
        date_str = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y") + " (сегодня)"
    
    elif period == "yesterday":
        date_from = today_start - timedelta(days=1)
        date_to = today_start - timedelta(seconds=1)
        date_str = (datetime.now(timezone.utc) + timedelta(hours=3) - timedelta(days=1)).strftime("%d.%m.%Y") + " (вчера)"
    
    elif period == "week":
        date_from = today_start - timedelta(days=7)
        date_to = now
        date_str = "7 дней"
    
    elif period == "month":
        date_from = today_start - timedelta(days=30)
        date_to = now
        date_str = "30 дней"
    
    elif period == "alltime":
        # Берём дату создания первого юзера как начало
        async with async_session() as session:
            first_user = await session.execute(
                select(User).order_by(User.created_at).limit(1)
            )
            first = first_user.scalar_one_or_none()
            date_from = first.created_at if first else now - timedelta(days=365)
        date_to = now
        date_str = "всё время"
    
    else:
        await callback.answer("❌ Неизвестный период")
        return
    
    # Показываем индикатор загрузки
    await callback.answer("⏳ Собираю данные...")
    
    # Собираем статистику
    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)
    
    # Форматируем сообщение
    message = format_report_message(data, date_str, is_all_time=(period == "alltime"))

    
    # 🔥 УМНАЯ ОТПРАВКА (разбиваем, если длиннее 4096 символов)
    if len(message) <= 4096:
        await callback.message.answer(message, parse_mode="HTML")
    else:
        # Разбиваем по строкам, чтобы не порвать HTML-теги
        current_part = ""
        for line in message.split('\n'):
            # Проверяем: если добавим эту строку, не превысим ли лимит?
            if len(current_part) + len(line) + 1 > 4096:
                await callback.message.answer(current_part, parse_mode="HTML")
                current_part = ""
            current_part += line + "\n"
        # Отправляем остаток
        if current_part:
            await callback.message.answer(current_part, parse_mode="HTML")

    # Показываем кнопки навигации для отчётов за один день
    if period in ["today", "yesterday"]:
        # Для сегодня/вчера добавляем навигацию
        report_date = now if period == "today" else now - timedelta(days=1)
        await callback.message.answer(
            "📊 Навигация по датам:",
            reply_markup=create_date_navigation_keyboard(report_date)
        )
    else:
        # Для недели/месяца/всего времени - обычные кнопки
        builder = InlineKeyboardBuilder()
        builder.button(text="📊 Ещё отчёт", callback_data="admin_stats_new")
        builder.button(text="🔙 В админку", callback_data="admin_menu")
        builder.adjust(1)
        await callback.message.answer("✅ Отчёт готов!", reply_markup=builder.as_markup())

@router.callback_query(F.data == "stats_custom")
async def cb_stats_custom_start(callback: types.CallbackQuery, state: FSMContext):
    """Запрос кастомного периода"""
    await state.set_state(StatsState.waiting_for_custom_dates)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_stats_new")
    
    await callback.message.edit_text(
        "🔧 <b>Свой период</b>\n\n"
        "Введите даты в формате:\n"
        "<code>ДД.ММ.ГГГГ - ДД.ММ.ГГГГ</code>\n\n"
        "Примеры:\n"
        "• <code>01.01.2026 - 09.01.2026</code>\n"
        "• <code>15.12.2025 - 31.12.2025</code>\n\n"
        "Или введите одну дату для отчёта за один день:\n"
        "• <code>05.01.2026</code>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(StatsState.waiting_for_custom_dates, F.text)
async def cb_stats_custom_process(message: types.Message, state: FSMContext):
    """Обработка кастомных дат"""
    text = message.text.strip()
    
    try:
        # Проверяем формат: одна дата или диапазон
        if " - " in text:
            # Диапазон дат
            date_from_str, date_to_str = text.split(" - ")
            date_from = datetime.strptime(date_from_str.strip(), "%d.%m.%Y").replace(hour=0, minute=0, second=0)  # ← Явно ставим начало дня
            date_to = datetime.strptime(date_to_str.strip(), "%d.%m.%Y").replace(hour=23, minute=59, second=59)
            date_str = f"{date_from_str.strip()} — {date_to_str.strip()}"
        else:
            # Одна дата
            date_from = datetime.strptime(text, "%d.%m.%Y").replace(hour=0, minute=0, second=0)  # ← И тут тоже
            date_to = date_from.replace(hour=23, minute=59, second=59)
            date_str = text
        
        # Показываем индикатор
        wait_msg = await message.answer("⏳ Собираю данные...")
        
        # Собираем статистику
        async with async_session() as session:
            data = await get_analytics_report(session, date_from, date_to)
        

        report = format_report_message(data, date_str)
        
        # Удаляем индикатор
        await wait_msg.delete()

        # 🔥 УМНАЯ ОТПРАВКА (разбиение длинного текста)
        if len(report) <= 4096:
            await message.answer(report, parse_mode="HTML")
        else:
            current_part = ""
            for line in report.split('\n'):
                if len(current_part) + len(line) + 1 > 4096:
                    await message.answer(current_part, parse_mode="HTML")
                    current_part = ""
                current_part += line + "\n"
            if current_part:
                await message.answer(current_part, parse_mode="HTML")
        
        # Очищаем состояние
        await state.clear()
        
        # ... (дальше код с кнопками оставляем как был) ...
        
        # Показываем кнопки навигации
        # Если это отчёт за один день - добавляем стрелки
        if " - " not in text:  # Один день
            await message.answer(
                "📊 Навигация по датам:",
                reply_markup=create_date_navigation_keyboard(date_from)
            )
        else:  # Диапазон дат
            builder = InlineKeyboardBuilder()
            builder.button(text="📊 Ещё отчёт", callback_data="admin_stats_new")
            builder.button(text="🔙 В админку", callback_data="admin_menu")
            builder.adjust(1)
            await message.answer("✅ Отчёт готов!", reply_markup=builder.as_markup())
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "Используйте:\n"
            "• <code>01.01.2026 - 09.01.2026</code> (диапазон)\n"
            "• <code>05.01.2026</code> (один день)",
            parse_mode="HTML"
        )

def get_today_start_msk():
    """
    Возвращает начало сегодняшнего дня по Москве (naive datetime).
    База хранит даты в MSK без timezone.
    """
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz)
    start_of_day_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day_msk.replace(tzinfo=None)

def create_date_navigation_keyboard(date: datetime):
    """Создаёт кнопки навигации по датам"""
    builder = InlineKeyboardBuilder()
    
    prev_date = date - timedelta(days=1)
    next_date = date + timedelta(days=1)
    
    # Форматируем даты для отображения
    prev_str = prev_date.strftime("%d.%m")
    next_str = next_date.strftime("%d.%m")
    
    # Для callback сохраняем в ISO формате
    date_iso = date.strftime("%Y-%m-%d")
    
    builder.button(text=f"◀️ {prev_str}", callback_data=f"stats_nav_{date_iso}_prev")
    builder.button(text="📅 Выбрать дату", callback_data="admin_stats_new")
    builder.button(text=f"{next_str} ▶️", callback_data=f"stats_nav_{date_iso}_next")
    builder.button(text="🔙 В админку", callback_data="admin_menu")
    
    builder.adjust(3, 1)
    return builder.as_markup()

@router.callback_query(F.data.startswith("stats_nav_"))
async def cb_stats_navigate(callback: types.CallbackQuery):
    """Навигация по датам в отчётах"""
    # Парсим callback: stats_nav_2026-01-12_prev
    parts = callback.data.split("_")
    date_str = parts[2]  # 2026-01-12
    direction = parts[3]  # prev или next
    
    current_date = datetime.strptime(date_str, "%Y-%m-%d")
    
    if direction == "prev":
        target_date = current_date - timedelta(days=1)
    else:  # next
        target_date = current_date + timedelta(days=1)
    
    # Формируем диапазон (весь день)
    date_from = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Форматируем дату для отображения
    date_str_display = target_date.strftime("%d.%m.%Y")
    
    # Определяем относительное описание
    now = datetime.now()
    if target_date.date() == now.date():
        date_str_display += " (сегодня)"
    elif target_date.date() == (now - timedelta(days=1)).date():
        date_str_display += " (вчера)"
    elif target_date.date() == (now + timedelta(days=1)).date():
        date_str_display += " (завтра)"
    
    # Показываем индикатор
    await callback.answer("⏳ Загружаю...")
    
    # Собираем статистику
    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)
    
    # Форматируем отчёт
    report = format_report_message(data, date_str_display)
    
    # Отправляем новым сообщением с кнопками навигации
    await callback.message.answer(report, parse_mode="HTML")
    await callback.message.answer(
        "📊 Навигация по датам:",
        reply_markup=create_date_navigation_keyboard(target_date)
    )

@router.callback_query(F.data == "admin_prompts")
async def cb_admin_prompts_menu(callback: types.CallbackQuery):
    """Главное меню статистики промптов"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 За сегодня", callback_data="prompts_today")
    builder.button(text="📅 За вчера", callback_data="prompts_yesterday")
    builder.button(text="📅 За неделю", callback_data="prompts_week")
    builder.button(text="📅 За месяц", callback_data="prompts_month")
    builder.button(text="📅 За всё время", callback_data="prompts_alltime")
    builder.button(text="🔧 Свой период", callback_data="prompts_custom")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(
        "🎨 <b>Статистика промптов</b>\n\n"
        "Выберите период для просмотра популярных промптов:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prompts_"))
async def cb_prompts_period(callback: types.CallbackQuery, state: FSMContext):
    """Показывает статистику промптов за период"""
    period = callback.data.split("_")[1]
    
    # Кастомный период - отдельный handler
    if period == "custom":
        await cb_prompts_custom_start(callback, state)
        return
    
    now = datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)
    today_start = get_today_start_msk()

    # Определяем период (как в отчётах)
    if period == "today":
        date_from = today_start
        date_to = now
        date_str = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y") + " (сегодня)"
    
    elif period == "yesterday":
        date_from = today_start - timedelta(days=1)
        date_to = today_start - timedelta(seconds=1)
        date_str = (datetime.now(timezone.utc) + timedelta(hours=3) - timedelta(days=1)).strftime("%d.%m.%Y") + " (вчера)"
    
    elif period == "week":
        date_from = today_start - timedelta(days=7)
        date_to = now
        date_str = "7 дней"
    
    elif period == "month":
        date_from = today_start - timedelta(days=30)
        date_to = now
        date_str = "30 дней"
    
    elif period == "alltime":
        async with async_session() as session:
            first_user = await session.execute(
                select(User).order_by(User.created_at).limit(1)
            )
            first = first_user.scalar_one_or_none()
            date_from = first.created_at if first else now - timedelta(days=365)
        date_to = now
        date_str = "всё время"
    
    else:
        await callback.answer("❌ Неизвестный период")
        return
    
    # Показываем индикатор
    await callback.answer("⏳ Собираю данные...")
    
    # Собираем статистику (только промпты!)
    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)
    
    # Форматируем ТОЛЬКО блок промптов
    prompt_campaigns = data.get('prompt_campaigns', [])
    
    if not prompt_campaigns:
        text = f"🎨 <b>Промпты за {date_str}</b>\n\n❌ Нет данных за этот период"
    else:
        text = f"🎨 <b>Промпты за {date_str}</b>\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        
        # Топ-10 промптов
        total_prompt_clicks = sum(p['clicks'] for p in prompt_campaigns)
        for i, prompt in enumerate(prompt_campaigns[:10], 1):
            if prompt['clicks'] > 0:
                percentage = (prompt['clicks'] / total_prompt_clicks * 100) if total_prompt_clicks > 0 else 0
                text += f"{i}. {prompt['name']} — {prompt['clicks']} ген. ({percentage:.1f}%)\n"
        
        # Итоговая статистика
        text += f"\n📊 Всего уникальных промптов: {len(prompt_campaigns)}\n"
        text += f"🔥 Всего генераций: {total_prompt_clicks}"
    
    # Отправляем
    await callback.message.answer(text, parse_mode="HTML")
    
    # Кнопки
    builder = InlineKeyboardBuilder()
    builder.button(text="🎨 Ещё период", callback_data="admin_prompts")
    builder.button(text="🔙 В админку", callback_data="admin_menu")
    builder.adjust(1)
    
    await callback.message.answer("✅ Готово!", reply_markup=builder.as_markup())


# Состояние для кастомного периода промптов
class PromptsState(StatesGroup):
    waiting_for_dates = State()


@router.callback_query(F.data == "prompts_custom")
async def cb_prompts_custom_start(callback: types.CallbackQuery, state: FSMContext):
    """Запрос кастомного периода для промптов"""
    await state.set_state(PromptsState.waiting_for_dates)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_prompts")
    
    await callback.message.edit_text(
        "🔧 <b>Свой период для промптов</b>\n\n"
        "Введите даты в формате:\n"
        "<code>ДД.ММ.ГГГГ - ДД.ММ.ГГГГ</code>\n\n"
        "Примеры:\n"
        "• <code>01.01.2026 - 09.01.2026</code>\n"
        "• <code>15.12.2025 - 31.12.2025</code>\n\n"
        "Или одну дату:\n"
        "• <code>05.01.2026</code>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(PromptsState.waiting_for_dates, F.text)
async def cb_prompts_custom_process(message: types.Message, state: FSMContext):
    """Обработка кастомных дат для промптов"""
    text = message.text.strip()
    
    try:
        # Парсим даты
        if " - " in text:
            date_from_str, date_to_str = text.split(" - ")
            date_from = datetime.strptime(date_from_str.strip(), "%d.%m.%Y")
            date_to = datetime.strptime(date_to_str.strip(), "%d.%m.%Y").replace(hour=23, minute=59, second=59)
            date_str = f"{date_from_str.strip()} — {date_to_str.strip()}"
        else:
            date_from = datetime.strptime(text, "%d.%m.%Y")
            date_to = date_from.replace(hour=23, minute=59, second=59)
            date_str = text
        
        # Показываем индикатор
        wait_msg = await message.answer("⏳ Собираю данные...")
        
        # Собираем статистику
        async with async_session() as session:
            data = await get_analytics_report(session, date_from, date_to)
        
        # Форматируем ТОЛЬКО промпты
        prompt_campaigns = data.get('prompt_campaigns', [])
        
        if not prompt_campaigns:
            report = f"🎨 <b>Промпты за {date_str}</b>\n\n❌ Нет данных за этот период"
        else:
            report = f"🎨 <b>Промпты за {date_str}</b>\n\n"
            report += "━━━━━━━━━━━━━━━━━━━━\n"
            
            total_prompt_clicks = sum(p['clicks'] for p in prompt_campaigns)
            for i, prompt in enumerate(prompt_campaigns[:10], 1):
                if prompt['clicks'] > 0:
                    percentage = (prompt['clicks'] / total_prompt_clicks * 100) if total_prompt_clicks > 0 else 0
                    report += f"{i}. {prompt['name']} — {prompt['clicks']} ген. ({percentage:.1f}%)\n"
            
            report += f"\n📊 Всего уникальных промптов: {len(prompt_campaigns)}\n"
            report += f"🔥 Всего генераций: {total_prompt_clicks}"
        
        # Удаляем индикатор и отправляем
        await wait_msg.delete()
        await message.answer(report, parse_mode="HTML")
        
        # Очищаем состояние
        await state.clear()
        
        # Кнопки
        builder = InlineKeyboardBuilder()
        builder.button(text="🎨 Ещё период", callback_data="admin_prompts")
        builder.button(text="🔙 В админку", callback_data="admin_menu")
        builder.adjust(1)
        
        await message.answer("✅ Готово!", reply_markup=builder.as_markup())
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "Используйте:\n"
            "• <code>01.01.2026 - 09.01.2026</code> (диапазон)\n"
            "• <code>05.01.2026</code> (один день)",
            parse_mode="HTML"
        )

        # =====================================================================
# 🐳 ГЛУБИНА ПЛАТЕЖЕЙ (Payment Depth)
# =====================================================================

@router.callback_query(F.data == "admin_payment_depth")
async def cb_payment_depth_menu(callback: types.CallbackQuery):
    """Меню выбора периода для Глубины платежей"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 За сегодня", callback_data="depth_today")
    builder.button(text="📅 За вчера", callback_data="depth_yesterday")
    builder.button(text="📅 За неделю", callback_data="depth_week")
    builder.button(text="📅 За месяц", callback_data="depth_month")
    builder.button(text="📅 За всё время", callback_data="depth_alltime")
    builder.button(text="🔧 Свой период", callback_data="depth_custom")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(
        "🐳 <b>Глубина платежей (LTV)</b>\n\n"
        "Этот отчет показывает, какой по счету была покупка.\n"
        "Помогает понять, сколько у нас новичков, а сколько китов.\n\n"
        "Выберите период:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

    # --- Кастомный период для Глубины ---

async def cb_depth_custom_start(callback: types.CallbackQuery, state: FSMContext):
    """Запрос дат для Глубины"""
    await state.set_state(PaymentDepthState.waiting_for_dates)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_payment_depth")
    
    await callback.message.edit_text(
        "🔧 <b>Глубина платежей: Свой период</b>\n\n"
        "Введите даты в формате:\n"
        "<code>ДД.ММ.ГГГГ - ДД.ММ.ГГГГ</code>\n\n"
        "Или одну дату:\n"
        "<code>05.01.2026</code>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("depth_"))
async def cb_payment_depth_period(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора периода для Глубины"""
    period = callback.data.split("_")[1]
    
    # Кастомный период обрабатываем отдельно
    if period == "custom":
        await cb_depth_custom_start(callback, state)
        return
    
    now = datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)
    today_start = get_today_start_msk()
    now_msk = datetime.now(timezone.utc) + timedelta(hours=3)

    # Логика дат (аналогична обычной статистике)
    if period == "today":
        date_from = today_start
        date_to = now
        date_label_start = now_msk.strftime("%d.%m.%Y")
        date_label_end = now_msk.strftime("%d.%m.%Y")
        
    elif period == "yesterday":
        date_from = today_start - timedelta(days=1)
        date_to = today_start - timedelta(seconds=1)
        yesterday_msk = now_msk - timedelta(days=1)
        date_label_start = yesterday_msk.strftime("%d.%m.%Y")
        date_label_end = yesterday_msk.strftime("%d.%m.%Y")
        
    elif period == "week":
        date_from = today_start - timedelta(days=7)
        date_to = now
        date_label_start = (now_msk - timedelta(days=7)).strftime("%d.%m.%Y")
        date_label_end = now_msk.strftime("%d.%m.%Y")

    elif period == "month":
        date_from = today_start - timedelta(days=30)
        date_to = now
        date_label_start = (now_msk - timedelta(days=30)).strftime("%d.%m.%Y")
        date_label_end = now_msk.strftime("%d.%m.%Y")
        
    elif period == "alltime":
        async with async_session() as session:
            first_purch = await session.execute(
                select(Purchase).order_by(Purchase.created_at).limit(1)
            )
            first = first_purch.scalar_one_or_none()
            date_from = first.created_at if first else now - timedelta(days=365)
        date_to = now
        date_label_start = date_from.strftime("%d.%m.%Y")
        date_label_end = now.strftime("%d.%m.%Y")
    else:
        await callback.answer("❌ Ошибка периода")
        return

    await callback.answer("⏳ Считаю воронку...")

    # Сбор данных
    async with async_session() as session:
        # Вызываем функцию, которую добавили в analytics_service
        data = await get_payment_depth_stats(session, date_from, date_to)

    # Форматирование
    text_general, text_sources = format_payment_depth_message(data, date_label_start, date_label_end)

    # Кнопки
    builder = InlineKeyboardBuilder()
    builder.button(text="🐳 Ещё период", callback_data="admin_payment_depth")
    builder.button(text="🔙 В админку", callback_data="admin_menu")
    builder.adjust(1)

    # Проверяем длину: если влезает в один - отправляем одним
    full_text = text_general + text_sources
    if len(full_text) <= 4000:  # Запас 96 символов на кнопки
        await callback.message.answer(full_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        # Разделяем на 2 сообщения
        await callback.message.answer(text_general, parse_mode="HTML")
        if text_sources:
            await callback.message.answer(text_sources, reply_markup=builder.as_markup(), parse_mode="HTML")
        else:
            await callback.message.answer("✅ Готово!", reply_markup=builder.as_markup())



@router.message(PaymentDepthState.waiting_for_dates, F.text)
async def cb_depth_custom_process(message: types.Message, state: FSMContext):
    """Обработка ввода дат для Глубины"""
    text = message.text.strip()
    
    try:
        if " - " in text:
            start_str, end_str = text.split(" - ")
            date_from = datetime.strptime(start_str.strip(), "%d.%m.%Y")
            date_to = datetime.strptime(end_str.strip(), "%d.%m.%Y").replace(hour=23, minute=59, second=59)
            date_label_start = start_str.strip()
            date_label_end = end_str.strip()
        else:
            date_from = datetime.strptime(text, "%d.%m.%Y")
            date_to = date_from.replace(hour=23, minute=59, second=59)
            date_label_start = text
            date_label_end = text

        wait_msg = await message.answer("⏳ Считаю воронку...")

        async with async_session() as session:
            data = await get_payment_depth_stats(session, date_from, date_to)
        
        # Формируем отчет. Передаем даты для заголовка
        text_general, text_sources = format_payment_depth_message(data, date_label_start, date_label_end)        
        await wait_msg.delete()

        # Проверяем длину
        full_text = text_general + text_sources
        if len(full_text) <= 4000:
            await message.answer(full_text, parse_mode="HTML")
        else:
            await message.answer(text_general, parse_mode="HTML")
            if text_sources:
                await message.answer(text_sources, parse_mode="HTML")

        await state.clear()
        
        # Кнопки возврата
        builder = InlineKeyboardBuilder()
        builder.button(text="🐳 Ещё период", callback_data="admin_payment_depth")
        builder.button(text="🔙 В админку", callback_data="admin_menu")
        builder.adjust(1)
        await message.answer("✅ Готово!", reply_markup=builder.as_markup())

    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат даты!</b>\n"
            "Попробуйте: <code>01.01.2026 - 31.01.2026</code>",
            parse_mode="HTML"
        )

@router.callback_query(F.data == "admin_kie_balance")
async def cb_kie_balance(callback: types.CallbackQuery):
    await callback.answer("⏳ Запрашиваю...")
    
    from app.services.kie_pricing import get_kie_balance
    balance = await get_kie_balance()
    
    if 'error' in balance:
        text = f"❌ Ошибка получения баланса:\n{balance['error']}"
    else:
        text = (
            f"💳 <b>Баланс kie.ai</b>\n\n"
            f"Кредитов: <b>{balance['credits']}</b>\n"
            f"В долларах: <b>${balance['usd']}</b>\n\n"
            f"💡 1 кредит = $0.005"
        )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Меню", callback_data="admin_menu")
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")