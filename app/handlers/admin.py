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
    waiting_for_aspect_ratio = State()  # 👈 ДОБАВЬ ЭТУ СТРОКУ
     # Ждём список кнопок
    waiting_for_confirmation = State()  # Показываем превью, ждём подтверждения


# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================
def get_admin_menu_kb():
    """Клавиатура главного меню админки"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🔍 Найти пользователя", callback_data="admin_find_user")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast") 
    builder.button(text="❌ Выйти", callback_data="close_admin")
    builder.adjust(1)
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
    print(f"👑 ADMIN LOG: Admin {admin_id} | Action: {action} | Target: {target_id}")
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
    
# 🔥 НОВЫЙ БЛОК - ЕСЛИ ЕСТЬ ПРОМПТ, СПРАШИВАЕМ ФОРМАТ 🔥
    if hidden_prompt:
        await state.set_state(BroadcastState.waiting_for_aspect_ratio)
        
        builder = InlineKeyboardBuilder()
        ratios = ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"]
        for r in ratios:
            builder.button(text=r, callback_data=f"bc_ratio_{r}")
        builder.adjust(3, 2, 2)
        
        await message.answer(
            "📐 <b>Выберите формат результата:</b>\n\n"
            "Это формат, в котором юзер получит изображение после генерации.\n"
            "💡 Выбирайте тот же формат, что на примере в рассылке!",
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
    
    safe_name = html.quote(str(user.full_name))
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
    
    text = (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"🆔 <code>{user.telegram_id}</code>\n"
        f"👤 Имя: {safe_name}\n"
        f"🔗 Ник: @{safe_username}\n\n"
        
        f"💎 <b>Баланс: {user.generations_balance} 🍌</b>\n"
        f"🎨 Генераций сделано: <b>{user_data['total_generations']}</b>\n\n"
        
        f"💰 <b>Платежи:</b>\n"
        f"  • Количество: {user_data['payments_count']}\n"
        f"  • Сумма: {user_data['payments_sum']}₽\n\n"
        
        f"🔗 <b>Источник:</b> {source_text}\n"
        f"👥 <b>Рефералов:</b> {user_data['referrals_count']}\n"
        f"🎁 <b>Бонусы:</b> {bonus_text}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить", callback_data=f"adm_add_{user.telegram_id}")
    builder.button(text="➖ Отнять", callback_data=f"adm_rem_{user.telegram_id}")
    builder.button(text="✉️ Написать", callback_data=f"adm_msg_{user.telegram_id}")
    builder.button(text="🔙 Меню", callback_data="admin_menu")
    builder.adjust(2, 1, 1)
    
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