# app/handlers/admin_scenarios.py
"""
Админка для управления рекламными сценариями (Deep Linking)
"""
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy import select, update
from app.database import async_session
from app.models import AdScenario, User
from app.config import ADMIN_IDS  # Список админов
from aiogram.exceptions import TelegramBadRequest

import re

router = Router()

# ============================================================
# STATES
# ============================================================

class AdminScenarioStates(StatesGroup):
    """Состояния для админки сценариев"""
    # Просмотр
    viewing_list = State()
    viewing_detail = State()
    
    # Создание нового сценария
    creating_key = State()
    creating_welcome = State()
    creating_prompt = State()
    creating_model = State()
    creating_ratio = State()
    
    # Редактирование
    editing_choose_field = State()
    editing_enter_value = State()

# ============================================================
# КОНСТАНТЫ
# ============================================================

AVAILABLE_MODELS = ["standard", "pro"]
AVAILABLE_RATIOS = ["1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "21:9"]

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    return user_id in ADMIN_IDS  # Проверяем что ID в списке

def get_scenario_stats_text(scenario: AdScenario) -> str:
    """Форматирование статистики сценария"""
    from app.config import BOT_USERNAME  # 👈 ИМПОРТ

    conversion = 0
    if scenario.total_starts > 0:
        conversion = (scenario.total_purchases / scenario.total_starts) * 100
    
    status_emoji = "✅" if scenario.is_active else "❌"
    
    return (
        f"🎨 <b>Сценарий:</b> {scenario.scenario_key}\n"
        f"Статус: {status_emoji} {'Активен' if scenario.is_active else 'Выключен'}\n\n"
        f"<b>Приветствие:</b>\n{scenario.welcome_text[:200]}{'...' if len(scenario.welcome_text) > 200 else ''}\n\n"
        f"<b>Промт:</b>\n{scenario.prompt[:200]}{'...' if len(scenario.prompt) > 200 else ''}\n\n"
        f"<b>Настройки:</b>\n"
        f"📱 Модель: {scenario.model_type}\n"
        f"📐 Формат: {scenario.aspect_ratio}\n\n"
        f"<b>📊 Статистика:</b>\n"
        f"👥 Переходов: {scenario.total_starts}\n"
        f"💰 Покупок: {scenario.total_purchases}\n"
        f"📈 Конверсия: {conversion:.1f}%\n\n"
        f"<b>🔗 Ссылка для рекламы:</b>\n"
        f"<code>https://t.me/{BOT_USERNAME}?start={scenario.scenario_key}_{{{{client_id}}}}</code>"  # 👈 ИСПРАВЛЕНО
    )

def get_model_buttons() -> InlineKeyboardMarkup:
    """Клавиатура выбора модели"""
    buttons = []
    for model in AVAILABLE_MODELS:
        emoji = "⚡" if model == "standard" else "💎"
        buttons.append([InlineKeyboardButton(
            text=f"{emoji} {model.capitalize()}",
            callback_data=f"scenario_model_{model}"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="scenario_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_ratio_buttons() -> InlineKeyboardMarkup:
    """Клавиатура выбора формата"""
    buttons = []
    row = []
    for i, ratio in enumerate(AVAILABLE_RATIOS):
        row.append(InlineKeyboardButton(text=ratio, callback_data=f"scenario_ratio_{ratio}"))
        if len(row) == 3:  # По 3 кнопки в ряд
            buttons.append(row)
            row = []
    if row:  # Добавляем оставшиеся
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="scenario_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_scenario_detail_buttons(scenario_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Кнопки для карточки сценария"""
    toggle_text = "⏸ Выключить" if is_active else "▶️ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"scenario_edit_{scenario_id}"),
            InlineKeyboardButton(text=toggle_text, callback_data=f"scenario_toggle_{scenario_id}")
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"scenario_delete_{scenario_id}")
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="scenario_list")
        ]
    ])

def get_edit_field_buttons(scenario_id: int) -> InlineKeyboardMarkup:
    """Кнопки выбора поля для редактирования"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Приветствие", callback_data=f"scenario_edit_field_{scenario_id}_welcome")],
        [InlineKeyboardButton(text="💬 Промт", callback_data=f"scenario_edit_field_{scenario_id}_prompt")],
        [InlineKeyboardButton(text="🎨 Модель", callback_data=f"scenario_edit_field_{scenario_id}_model")],
        [InlineKeyboardButton(text="📐 Формат", callback_data=f"scenario_edit_field_{scenario_id}_ratio")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"scenario_view_{scenario_id}")]
    ])

# ============================================================
# ХЭНДЛЕРЫ - ПРОСМОТР
# ============================================================

@router.message(Command("admin_scenarios"))
async def cmd_admin_scenarios(message: types.Message, state: FSMContext):
    """Главное меню управления сценариями"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Недостаточно прав")
        return
    
    await state.clear()
    
    async with async_session() as session:
        # Получаем все сценарии
        result = await session.execute(
            select(AdScenario).order_by(AdScenario.is_active.desc(), AdScenario.created_at.desc())
        )
        scenarios = result.scalars().all()
        
        if not scenarios:
            text = "📋 <b>Рекламные сценарии</b>\n\n❌ Сценариев пока нет"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать первый сценарий", callback_data="scenario_create")]
            ])
        else:
            active_scenarios = [s for s in scenarios if s.is_active]
            inactive_scenarios = [s for s in scenarios if not s.is_active]
            
            text = "📋 <b>Управление рекламными сценариями</b>\n\n"
            
            if active_scenarios:
                text += "<b>Активные:</b>\n"
                for s in active_scenarios:
                    conv = (s.total_purchases / s.total_starts * 100) if s.total_starts > 0 else 0
                    text += f"✅ {s.scenario_key} ({s.total_starts} переходов, {s.total_purchases} покупок, {conv:.1f}%)\n"
                text += "\n"
            
            if inactive_scenarios:
                text += "<b>Неактивные:</b>\n"
                for s in inactive_scenarios:
                    conv = (s.total_purchases / s.total_starts * 100) if s.total_starts > 0 else 0
                    text += f"❌ {s.scenario_key} ({s.total_starts} переходов, {s.total_purchases} покупок, {conv:.1f}%)\n"
            
            # Создаем кнопки для каждого сценария
            buttons = []
            for s in scenarios:
                emoji = "✅" if s.is_active else "❌"
                buttons.append([InlineKeyboardButton(
                    text=f"{emoji} {s.scenario_key}",
                    callback_data=f"scenario_view_{s.id}"
                )])
            buttons.append([InlineKeyboardButton(text="➕ Создать новый", callback_data="scenario_create")])
            buttons.append([InlineKeyboardButton(text="📊 Общая статистика", callback_data="scenario_stats")])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        await state.set_state(AdminScenarioStates.viewing_list)

@router.callback_query(F.data == "scenario_list")
async def callback_scenario_list(callback: CallbackQuery, state: FSMContext):
    """Возврат к списку сценариев"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    # УБЕРИ ЭТУ СТРОКУ:
    # await callback.message.delete()
    
    # Используем тот же код что и в cmd_admin_scenarios
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).order_by(AdScenario.is_active.desc(), AdScenario.created_at.desc())
        )
        scenarios = result.scalars().all()
        
        if not scenarios:
            text = "📋 <b>Рекламные сценарии</b>\n\n❌ Сценариев пока нет"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать первый сценарий", callback_data="scenario_create")],
                [InlineKeyboardButton(text="◀️ Назад в админку", callback_data="back_to_admin")]  # 👈 ДОБАВЬ
            ])
        else:
            active_scenarios = [s for s in scenarios if s.is_active]
            inactive_scenarios = [s for s in scenarios if not s.is_active]
            
            text = "📋 <b>Управление рекламными сценариями</b>\n\n"
            
            if active_scenarios:
                text += "<b>Активные:</b>\n"
                for s in active_scenarios:
                    conv = (s.total_purchases / s.total_starts * 100) if s.total_starts > 0 else 0
                    text += f"✅ {s.scenario_key} ({s.total_starts} переходов, {s.total_purchases} покупок, {conv:.1f}%)\n"
                text += "\n"
            
            if inactive_scenarios:
                text += "<b>Неактивные:</b>\n"
                for s in inactive_scenarios:
                    conv = (s.total_purchases / s.total_starts * 100) if s.total_starts > 0 else 0
                    text += f"❌ {s.scenario_key} ({s.total_starts} переходов, {s.total_purchases} покупок, {conv:.1f}%)\n"
            
            buttons = []
            for s in scenarios:
                emoji = "✅" if s.is_active else "❌"
                buttons.append([InlineKeyboardButton(
                    text=f"{emoji} {s.scenario_key}",
                    callback_data=f"scenario_view_{s.id}"
                )])
            buttons.append([InlineKeyboardButton(text="➕ Создать новый", callback_data="scenario_create")])
            buttons.append([InlineKeyboardButton(text="📊 Общая статистика", callback_data="scenario_stats")])
            buttons.append([InlineKeyboardButton(text="◀️ Назад в админку", callback_data="back_to_admin")])  # 👈 ДОБАВЬ
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # ЗАМЕНИ delete() на edit_text()
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            if "message is not modified" in str(e).lower():
                await callback.answer()
            else:
                # Если не получилось отредактировать - отправим новое
                await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        
        await state.set_state(AdminScenarioStates.viewing_list)
    
    await callback.answer()

@router.callback_query(F.data.startswith("scenario_view_"))
async def callback_view_scenario(callback: CallbackQuery, state: FSMContext):
    """Просмотр детальной карточки сценария"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    scenario_id = int(callback.data.split("_")[-1])
    
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).where(AdScenario.id == scenario_id)
        )
        scenario = result.scalar_one_or_none()
        
        if not scenario:
            await callback.answer("❌ Сценарий не найден", show_alert=True)
            return
        
        text = get_scenario_stats_text(scenario)
        keyboard = get_scenario_detail_buttons(scenario.id, scenario.is_active)
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        await state.set_state(AdminScenarioStates.viewing_detail)
        await state.update_data(current_scenario_id=scenario_id)
    
    await callback.answer()

    # ============================================================
# ХЭНДЛЕРЫ - СОЗДАНИЕ СЦЕНАРИЯ
# ============================================================

@router.callback_query(F.data == "scenario_create")
async def callback_create_scenario(callback: CallbackQuery, state: FSMContext):
    """Начало создания нового сценария"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    from app.config import BOT_USERNAME  # 👈 ДОБАВЬ ИМПОРТ
    
    text = (
        "➕ <b>Создание нового сценария</b>\n\n"
        "<b>Шаг 1/5: Ключ сценария</b>\n\n"
        "Введи уникальный ключ для ссылки (латиница, цифры, подчеркивание).\n"
        "Например: <code>love_is</code>, <code>cyberpunk</code>, <code>winter_magic</code>\n\n"
        "Этот ключ будет использоваться в ссылке:\n"
        f"<code>t.me/{BOT_USERNAME}?start=ВАШ_КЛЮЧ_{{{{client_id}}}}</code>"  # 👈 ИСПРАВЛЕНО
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="scenario_list")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(AdminScenarioStates.creating_key)
    await callback.answer()

@router.message(AdminScenarioStates.creating_key)
async def process_creating_key(message: types.Message, state: FSMContext):
    """Обработка ввода ключа"""
    key = message.text.strip().lower()
    
    # Валидация
    if not re.match(r'^[a-z0-9_]+$', key):
        await message.answer(
            "❌ Неверный формат!\n\n"
            "Используй только латинские буквы, цифры и подчеркивание.\n"
            "Например: <code>love_is</code> или <code>summer_2024</code>",
            parse_mode="HTML"
        )
        return
    
    # Проверка уникальности
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).where(AdScenario.scenario_key == key)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            await message.answer(
                f"❌ Сценарий с ключом <code>{key}</code> уже существует!\n\n"
                "Придумай другое название.",
                parse_mode="HTML"
            )
            return
    
    # Сохраняем ключ
    await state.update_data(scenario_key=key)
    
    text = (
        f"✅ Ключ сохранен: <code>{key}</code>\n\n"
        "<b>Шаг 2/5: Приветственное сообщение</b>\n\n"
        "Напиши текст, который увидит пользователь при переходе по ссылке.\n\n"
        "Можешь использовать HTML-теги: <code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>\n"
        "Эмодзи приветствуются! 🎉"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="scenario_cancel")]
    ])
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(AdminScenarioStates.creating_welcome)

@router.message(AdminScenarioStates.creating_welcome)
async def process_creating_welcome(message: types.Message, state: FSMContext):
    """Обработка приветственного текста"""
    welcome_text = message.text.strip()
    
    if len(welcome_text) < 10:
        await message.answer("❌ Слишком короткое приветствие! Минимум 10 символов.")
        return
    
    if len(welcome_text) > 2000:
        await message.answer("❌ Слишком длинное сообщение! Максимум 2000 символов.")
        return
    
    await state.update_data(welcome_text=welcome_text)
    
    text = (
        "✅ Приветствие сохранено!\n\n"
        "<b>Шаг 3/5: Промт для генерации</b>\n\n"
        "Напиши промт, который будет автоматически применяться к фото пользователя.\n\n"
        "Например:\n"
        "<code>Romantic couple in Love Is gum wrapper style, cartoon illustration, bright colors</code>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="scenario_cancel")]
    ])
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(AdminScenarioStates.creating_prompt)

@router.message(AdminScenarioStates.creating_prompt)
async def process_creating_prompt(message: types.Message, state: FSMContext):
    """Обработка промта"""
    prompt = message.text.strip()
    
    if len(prompt) < 10:
        await message.answer("❌ Слишком короткий промт! Минимум 10 символов.")
        return
    
    if len(prompt) > 2000:
        await message.answer("❌ Слишком длинный промт! Максимум 2000 символов.")
        return
    
    await state.update_data(prompt=prompt)
    
    text = (
        "✅ Промт сохранен!\n\n"
        "<b>Шаг 4/5: Модель генерации</b>\n\n"
        "Выбери модель:"
    )
    
    await message.answer(text, parse_mode="HTML", reply_markup=get_model_buttons())
    await state.set_state(AdminScenarioStates.creating_model)

@router.callback_query(AdminScenarioStates.creating_model, F.data.startswith("scenario_model_"))
async def process_creating_model(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора модели"""
    model = callback.data.split("_")[-1]
    
    await state.update_data(model_type=model)
    
    text = (
        f"✅ Модель выбрана: <b>{model}</b>\n\n"
        "<b>Шаг 5/5: Формат изображения</b>\n\n"
        "Выбери соотношение сторон:"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_ratio_buttons())
    await state.set_state(AdminScenarioStates.creating_ratio)
    await callback.answer()

@router.callback_query(AdminScenarioStates.creating_ratio, F.data.startswith("scenario_ratio_"))
async def process_creating_ratio(callback: CallbackQuery, state: FSMContext):
    """Финальный шаг - создание сценария"""
    ratio = callback.data.replace("scenario_ratio_", "")
    
    data = await state.get_data()
    
    from app.config import BOT_USERNAME  # 👈 ДОБАВЬ ИМПОРТ
    
    # Создаем сценарий в БД
    async with async_session() as session:
        new_scenario = AdScenario(
            scenario_key=data['scenario_key'],
            welcome_text=data['welcome_text'],
            prompt=data['prompt'],
            model_type=data['model_type'],
            aspect_ratio=ratio,
            is_active=True
        )
        session.add(new_scenario)
        await session.commit()
        await session.refresh(new_scenario)
        
        scenario_id = new_scenario.id
    
    text = (
        "✅ <b>Сценарий успешно создан!</b>\n\n"
        f"🔗 Используй эту ссылку в рекламе:\n"
        f"<code>https://t.me/{BOT_USERNAME}?start={data['scenario_key']}_{{{{client_id}}}}</code>\n\n"  # 👈 ИСПРАВЛЕНО
        "Где <code>{{{{client_id}}}}</code> — динамический параметр из Яндекс.Метрики"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Посмотреть сценарий", callback_data=f"scenario_view_{scenario_id}")],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="scenario_list")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await state.clear()
    await callback.answer("🎉 Сценарий создан!")

# ============================================================
# ХЭНДЛЕРЫ - УПРАВЛЕНИЕ СЦЕНАРИЯМИ
# ============================================================

@router.callback_query(F.data.startswith("scenario_toggle_"))
async def callback_toggle_scenario(callback: CallbackQuery, state: FSMContext):
    """Включение/выключение сценария"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    scenario_id = int(callback.data.split("_")[-1])
    
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).where(AdScenario.id == scenario_id)
        )
        scenario = result.scalar_one_or_none()
        
        if not scenario:
            await callback.answer("❌ Сценарий не найден", show_alert=True)
            return
        
        # Переключаем статус
        scenario.is_active = not scenario.is_active
        await session.commit()
        
        status = "включен" if scenario.is_active else "выключен"
        
        # Обновляем карточку
        text = get_scenario_stats_text(scenario)
        keyboard = get_scenario_detail_buttons(scenario.id, scenario.is_active)
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        await callback.answer(f"✅ Сценарий {status}")

@router.callback_query(F.data.startswith("scenario_delete_"), ~F.data.contains("confirm"))
async def callback_delete_scenario(callback: CallbackQuery, state: FSMContext):
    """Удаление сценария с подтверждением"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    scenario_id = int(callback.data.split("_")[-1])
    
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).where(AdScenario.id == scenario_id)
        )
        scenario = result.scalar_one_or_none()
        
        if not scenario:
            await callback.answer("❌ Сценарий не найден", show_alert=True)
            return
        
        text = (
            f"⚠️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
            f"Ключ: <code>{scenario.scenario_key}</code>\n"
            f"Переходов: {scenario.total_starts}\n"
            f"Покупок: {scenario.total_purchases}\n\n"
            f"❗️ <b>Это действие нельзя отменить!</b>\n"
            f"Удалить сценарий?"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить навсегда", callback_data=f"scenario_delete_confirm_{scenario_id}")],
            [InlineKeyboardButton(text="❌ Нет, оставить", callback_data=f"scenario_view_{scenario_id}")]
        ])
        
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            if "message is not modified" in str(e).lower():
                # Если сообщение не изменилось - просто показываем alert
                await callback.answer("⚠️ Подтверди удаление кнопкой ниже", show_alert=True)
            else:
                raise
    
    await callback.answer()
    

@router.callback_query(F.data.startswith("scenario_delete_confirm_"))
async def callback_delete_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    scenario_id = int(callback.data.split("_")[-1])
    
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).where(AdScenario.id == scenario_id)
        )
        scenario = result.scalar_one_or_none()
        
        if scenario:
            await session.delete(scenario)
            await session.commit()
    
    await callback.message.delete()
    await callback.answer("✅ Сценарий удален")
    
    # Показываем список
    await callback_scenario_list(callback, state)

    # ============================================================
# ХЭНДЛЕРЫ - РЕДАКТИРОВАНИЕ
# ============================================================

@router.callback_query(F.data.startswith("scenario_edit_"), ~F.data.contains("field"))
async def callback_edit_scenario(callback: CallbackQuery, state: FSMContext):
    """Меню выбора поля для редактирования"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    # Парсим ID: scenario_edit_5
    scenario_id = int(callback.data.split("_")[-1])
    
    text = "✏️ <b>Что хочешь изменить?</b>"
    keyboard = get_edit_field_buttons(scenario_id)
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(AdminScenarioStates.editing_choose_field)
    await state.update_data(editing_scenario_id=scenario_id)
    await callback.answer()

@router.callback_query(AdminScenarioStates.editing_choose_field, F.data.startswith("scenario_edit_field_"))
async def callback_edit_field(callback: CallbackQuery, state: FSMContext):
    """Выбор поля и переход к вводу/выбору"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    parts = callback.data.split("_")
    scenario_id = int(parts[3])
    field = parts[4]  # welcome, prompt, model, ratio
    
    await state.update_data(editing_field=field, editing_scenario_id=scenario_id)
    
    if field == "welcome":
        text = (
            "📝 <b>Редактирование приветствия</b>\n\n"
            "Отправь новый текст приветственного сообщения:"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"scenario_view_{scenario_id}")]
        ])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        await state.set_state(AdminScenarioStates.editing_enter_value)
    
    elif field == "prompt":
        text = (
            "💬 <b>Редактирование промта</b>\n\n"
            "Отправь новый промт для генерации:"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"scenario_view_{scenario_id}")]
        ])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        await state.set_state(AdminScenarioStates.editing_enter_value)
    
    elif field == "model":
        text = "🎨 <b>Выбери новую модель:</b>"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_model_buttons())
        await state.set_state(AdminScenarioStates.editing_enter_value)
    
    elif field == "ratio":
        text = "📐 <b>Выбери новый формат:</b>"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_ratio_buttons())
        await state.set_state(AdminScenarioStates.editing_enter_value)
    
    await callback.answer()

@router.message(AdminScenarioStates.editing_enter_value)
async def process_edit_text_value(message: types.Message, state: FSMContext):
    """Обработка текстового ввода при редактировании"""
    data = await state.get_data()
    scenario_id = data['editing_scenario_id']
    field = data['editing_field']
    new_value = message.text.strip()
    
    # Валидация
    if len(new_value) < 10:
        await message.answer("❌ Слишком короткий текст! Минимум 10 символов.")
        return
    
    if len(new_value) > 2000:
        await message.answer("❌ Слишком длинный текст! Максимум 2000 символов.")
        return
    
    # Обновляем в БД
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).where(AdScenario.id == scenario_id)
        )
        scenario = result.scalar_one_or_none()
        
        if not scenario:
            await message.answer("❌ Сценарий не найден")
            await state.clear()
            return
        
        if field == "welcome":
            scenario.welcome_text = new_value
        elif field == "prompt":
            scenario.prompt = new_value
        
        await session.commit()
        
        # Показываем обновленную карточку
        text = get_scenario_stats_text(scenario)
        keyboard = get_scenario_detail_buttons(scenario.id, scenario.is_active)
        
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        await state.clear()

@router.callback_query(AdminScenarioStates.editing_enter_value, F.data.startswith("scenario_model_"))
async def process_edit_model(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора модели при редактировании"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    model = callback.data.split("_")[-1]
    data = await state.get_data()
    scenario_id = data['editing_scenario_id']
    
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).where(AdScenario.id == scenario_id)
        )
        scenario = result.scalar_one_or_none()
        
        if not scenario:
            await callback.answer("❌ Сценарий не найден", show_alert=True)
            await state.clear()
            return
        
        scenario.model_type = model
        await session.commit()
        
        text = get_scenario_stats_text(scenario)
        keyboard = get_scenario_detail_buttons(scenario.id, scenario.is_active)
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        await state.clear()
        await callback.answer(f"✅ Модель изменена на {model}")

@router.callback_query(AdminScenarioStates.editing_enter_value, F.data.startswith("scenario_ratio_"))
async def process_edit_ratio(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора формата при редактировании"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    ratio = callback.data.replace("scenario_ratio_", "")
    data = await state.get_data()
    scenario_id = data['editing_scenario_id']
    
    async with async_session() as session:
        result = await session.execute(
            select(AdScenario).where(AdScenario.id == scenario_id)
        )
        scenario = result.scalar_one_or_none()
        
        if not scenario:
            await callback.answer("❌ Сценарий не найден", show_alert=True)
            await state.clear()
            return
        
        scenario.aspect_ratio = ratio
        await session.commit()
        
        text = get_scenario_stats_text(scenario)
        keyboard = get_scenario_detail_buttons(scenario.id, scenario.is_active)
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        await state.clear()
        await callback.answer(f"✅ Формат изменен на {ratio}")

# ============================================================
# ХЭНДЛЕРЫ - ОТМЕНА И СТАТИСТИКА
# ============================================================

@router.callback_query(F.data == "scenario_cancel")
async def callback_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена текущего действия"""
    await state.clear()
    await callback.message.delete()
    await callback.answer("❌ Отменено")
    
    # Возвращаем к списку
    await callback_scenario_list(callback, state)

@router.callback_query(F.data == "scenario_stats")
async def callback_stats(callback: CallbackQuery, state: FSMContext):
    """Общая статистика по всем сценариям"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    async with async_session() as session:
        result = await session.execute(select(AdScenario))
        scenarios = result.scalars().all()
        
        if not scenarios:
            await callback.answer("❌ Нет сценариев для статистики", show_alert=True)
            return
        
        total_starts = sum(s.total_starts for s in scenarios)
        total_purchases = sum(s.total_purchases for s in scenarios)
        avg_conversion = (total_purchases / total_starts * 100) if total_starts > 0 else 0
        
        text = (
            "📊 <b>Общая статистика</b>\n\n"
            f"Всего сценариев: {len(scenarios)}\n"
            f"Активных: {sum(1 for s in scenarios if s.is_active)}\n\n"
            f"👥 Всего переходов: {total_starts}\n"
            f"💰 Всего покупок: {total_purchases}\n"
            f"📈 Средняя конверсия: {avg_conversion:.1f}%\n\n"
            "<b>Топ-3 по конверсии:</b>\n"
        )
        
        # Сортируем по конверсии
        sorted_scenarios = sorted(
            [s for s in scenarios if s.total_starts > 0],
            key=lambda x: (x.total_purchases / x.total_starts) if x.total_starts > 0 else 0,
            reverse=True
        )[:3]
        
        for i, s in enumerate(sorted_scenarios, 1):
            conv = (s.total_purchases / s.total_starts * 100) if s.total_starts > 0 else 0
            text += f"{i}. {s.scenario_key}: {conv:.1f}% ({s.total_purchases}/{s.total_starts})\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="scenario_list")]
        ])
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    
    await callback.answer()

    # ============================================================
# ИНТЕГРАЦИЯ С АДМИН-МЕНЮ
# ============================================================

@router.callback_query(F.data == "admin_scenarios_menu")
async def callback_admin_scenarios_menu(callback: CallbackQuery, state: FSMContext):
    """Вход в меню сценариев из админ-панели"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    await state.clear()
    
    async with async_session() as session:
        # Получаем все сценарии
        result = await session.execute(
            select(AdScenario).order_by(AdScenario.is_active.desc(), AdScenario.created_at.desc())
        )
        scenarios = result.scalars().all()
        
        if not scenarios:
            text = "📋 <b>Рекламные сценарии</b>\n\n❌ Сценариев пока нет"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать первый сценарий", callback_data="scenario_create")],
                [InlineKeyboardButton(text="◀️ Назад в админку", callback_data="back_to_admin")]
            ])
        else:
            active_scenarios = [s for s in scenarios if s.is_active]
            inactive_scenarios = [s for s in scenarios if not s.is_active]
            
            text = "📋 <b>Управление рекламными сценариями</b>\n\n"
            
            if active_scenarios:
                text += "<b>Активные:</b>\n"
                for s in active_scenarios:
                    conv = (s.total_purchases / s.total_starts * 100) if s.total_starts > 0 else 0
                    text += f"✅ {s.scenario_key} ({s.total_starts} переходов, {s.total_purchases} покупок, {conv:.1f}%)\n"
                text += "\n"
            
            if inactive_scenarios:
                text += "<b>Неактивные:</b>\n"
                for s in inactive_scenarios:
                    conv = (s.total_purchases / s.total_starts * 100) if s.total_starts > 0 else 0
                    text += f"❌ {s.scenario_key} ({s.total_starts} переходов, {s.total_purchases} покупок, {conv:.1f}%)\n"
            
            # Создаем кнопки для каждого сценария
            buttons = []
            for s in scenarios:
                emoji = "✅" if s.is_active else "❌"
                buttons.append([InlineKeyboardButton(
                    text=f"{emoji} {s.scenario_key}",
                    callback_data=f"scenario_view_{s.id}"
                )])
            buttons.append([InlineKeyboardButton(text="➕ Создать новый", callback_data="scenario_create")])
            buttons.append([InlineKeyboardButton(text="📊 Общая статистика", callback_data="scenario_stats")])
            buttons.append([InlineKeyboardButton(text="◀️ Назад в админку", callback_data="back_to_admin")])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

                    # 🔥 ДОБАВЬ TRY-EXCEPT
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            if "message is not modified" in str(e).lower():
                await callback.answer()
            else:
                raise
        
    # admin_scenarios.py, строка ~892
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except TelegramBadRequest:
        pass
        await state.set_state(AdminScenarioStates.viewing_list)
    
    await callback.answer()

@router.callback_query(F.data == "back_to_admin")
async def callback_back_to_admin(callback: CallbackQuery, state: FSMContext):
    """Возврат в админ-панель"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    await state.clear()
    
    # Импортируем функцию админ-меню
    from app.handlers.admin import get_admin_menu_kb
    
    text = "🔧 <b>Админ-панель</b>\n\nВыбери действие:"
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_menu_kb())
    await callback.answer()