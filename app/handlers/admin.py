from aiogram import Router, types, F, Bot, html
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.config import ADMIN_IDS
from app.database import async_session
from app.services.user_service import get_bot_stats, find_user_by_input, admin_change_balance
from app.services.payment_service import confirm_purchase
from app.handlers.start import get_main_kb
# 👇 ДОБАВИТЬ ЭТИ ИМПОРТЫ
from sqlalchemy import select, func
from app.models import User, Purchase

router = Router()


# --- СОСТОЯНИЯ АДМИНА ---
class AdminState(StatesGroup):
    waiting_for_user_search = State()
    waiting_for_balance_change = State()
    waiting_for_message = State()


# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================
def get_admin_menu_kb():
    """Клавиатура главного меню админки"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🔍 Найти пользователя", callback_data="admin_find_user")
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
        user = await find_user_by_input(session, user_input)
    
    if not user:
        await message.answer(
            "❌ Пользователь не найден.\n"
            "Попробуй еще раз или жми /admin",
            reply_markup=get_cancel_kb()
        )
        return

    await log_admin_action(message.from_user.id, "found_user", user.telegram_id)
    
    await state.clear()
    await show_user_card(message, user.telegram_id, user.full_name, user.username, user.generations_balance)


async def show_user_card(message: types.Message, user_id: int, name: str, username: str, balance: int):
    """Показывает карточку пользователя с действиями"""
    safe_name = html.quote(str(name))
    safe_username = html.quote(str(username)) if username else "Нет"
    
    text = (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"🆔 <code>{user_id}</code>\n"
        f"👤 Имя: {safe_name}\n"
        f"🔗 Ник: @{safe_username}\n\n"
        f"💎 <b>Баланс: {balance} 🍌</b>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить", callback_data=f"adm_add_{user_id}")
    builder.button(text="➖ Отнять", callback_data=f"adm_rem_{user_id}")
    builder.button(text="✉️ Написать", callback_data=f"adm_msg_{user_id}")
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