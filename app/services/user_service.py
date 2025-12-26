from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Purchase
from datetime import datetime, timedelta
from app.config import START_BALANCE
from app.models import MessageHistory, GenerationTask

async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str | None, full_name: str | None):
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    if user:
        return user, False

    new_user = User(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
        generations_balance=START_BALANCE # <--- Берем из конфига
    )
    session.add(new_user)
    await session.commit()
    return new_user, True

async def check_and_deduct_balance(session: AsyncSession, telegram_id: int, amount: int = 1) -> bool:
    """
    Списывает указанное количество генераций (amount).
    Сначала списывает с balance_paid, потом с balance_free.
    Возвращает True, если баланса хватило.
    """
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        return False
    
    # Проверяем общий баланс
    total_balance = user.balance_paid + user.balance_free
    if total_balance < amount:
        return False  # Недостаточно бананов
    
    # Списываем сначала с платных, потом с бесплатных
    remaining = amount
    
    if user.balance_paid > 0:
        deduct_from_paid = min(user.balance_paid, remaining)
        user.balance_paid -= deduct_from_paid
        remaining -= deduct_from_paid
    
    if remaining > 0:
        user.balance_free -= remaining
    
    # Обновляем старое поле для совместимости
    user.generations_balance = user.balance_paid + user.balance_free
    
    # Статистика
    user.total_generations_used += 1
    
    # Обновляем дату последней генерации
    from datetime import datetime
    user.last_generation_at = datetime.now()
    
    await session.commit()
    return True

async def get_user_balance(session: AsyncSession, telegram_id: int) -> int:
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    return user.generations_balance if user else 0

async def claim_bonus(session: AsyncSession, user_id: int, amount: int = 5) -> bool:
    query = select(User).where(User.telegram_id == user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    if user and not user.is_sub_bonus_claimed:
        user.generations_balance += amount
        user.balance_free += amount  # ← ДОБАВИЛИ
        user.is_sub_bonus_claimed = True
        await session.commit()
        return True
    return False

async def get_bot_stats(session: AsyncSession):
    users_count = await session.scalar(select(func.count(User.id)))
    gens_count = await session.scalar(select(func.sum(User.total_generations_used))) or 0
    money = await session.scalar(select(func.sum(Purchase.price)).where(Purchase.status == 'paid')) or 0
    
    return {
        "users": users_count,
        "gens": gens_count,
        "money": money
    }

async def get_user_profile_data(session: AsyncSession, user_id: int):
    query_user = select(User).where(User.telegram_id == user_id)
    result_user = await session.execute(query_user)
    user = result_user.scalar_one_or_none()

    if not user: return None

    query_spent = select(func.sum(Purchase.price)).where(Purchase.user_id == user_id).where(Purchase.status == 'paid')
    total_spent = await session.scalar(query_spent) or 0

    query_purchases = (
        select(Purchase)
        .where(Purchase.user_id == user_id)
        .where(Purchase.status == 'paid')
        .order_by(desc(Purchase.created_at))
        .limit(2)
    )
    purchases_result = await session.execute(query_purchases)
    last_purchases = purchases_result.scalars().all()

    return {
        "user": user,
        "total_spent": total_spent,
        "last_purchases": last_purchases
    }

# 👇 ВОТ ЭТА ФУНКЦИЯ, КОТОРОЙ НЕ ХВАТАЛО
async def is_user_premium(session: AsyncSession, user_id: int) -> bool:
    """Проверяет, были ли у пользователя оплаченные покупки"""
    query = select(Purchase).where(Purchase.user_id == user_id, Purchase.status == "paid")
    result = await session.execute(query)
    # Если нашли хоть одну запись - значит премиум
    return result.first() is not None

async def find_user_by_input(session: AsyncSession, user_input: str):
    """
    Универсальный поиск: ищет совпадение ИЛИ по ID, ИЛИ по Username.
    """
    # Чистим ввод от пробелов и знака @
    clean_input = str(user_input).strip().replace("@", "")
    
    print(f"🔍 Ищу пользователя: '{clean_input}'") # Отладка в консоль

    conditions = []
    
    # 1. Всегда ищем по юзернейму
    conditions.append(User.username == clean_input)
    
    # 2. Если это число, добавляем поиск по ID
    if clean_input.isdigit():
        conditions.append(User.telegram_id == int(clean_input))
    
    # Запрос: "Найди, где (username = X) ИЛИ (telegram_id = X)"
    query = select(User).where(or_(*conditions))
    
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    
    if user:
        print(f"✅ Нашел: ID {user.telegram_id} | Name: {user.full_name}")
    else:
        print("❌ Не нашел в базе.")
        
    return user

async def admin_change_balance(session: AsyncSession, user_id: int, amount: int):
    """
    Меняет баланс пользователя (может быть отрицательным числом).
    Админ начисляет/списывает бесплатные бананы (balance_free).
    """
    query = select(User).where(User.telegram_id == user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    
    if user:
        # Админ работает с бесплатными бананами
        user.balance_free += amount
        
        # Защита от ухода в минус
        if user.balance_free < 0:
            user.balance_free = 0
        
        # Синхронизируем старое поле
        user.generations_balance = user.balance_paid + user.balance_free
        
        await session.commit()
        return user.generations_balance
    return None


# Замени функцию add_history
async def add_history(session: AsyncSession, user_id: int, role: str, content: str, has_image: bool = False, file_id: str = None, image_url: str = None):
    # Добавили image_url=image_url
    msg = MessageHistory(user_id=user_id, role=role, content=content, has_image=has_image, file_id=file_id, image_url=image_url)
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg

async def get_dialog_context(session: AsyncSession, user_id: int, limit: int = 6):
    """
    Возвращает последние N сообщений диалога.
    limit=6 значит 3 пары вопрос-ответ.
    """
    query = (
        select(MessageHistory)
        .where(MessageHistory.user_id == user_id)
        .order_by(desc(MessageHistory.created_at))
        .limit(limit)
    )
    result = await session.execute(query)
    # Переворачиваем, чтобы старые были в начале (для контекста)
    return result.scalars().all()[::-1]

async def clear_history(session: AsyncSession, user_id: int):
    """Очистить контекст (кнопка 'Забыть' или 'Новый диалог')"""
    # В sqlite проще удалить, или можно ставить флаг is_archived
    # Для MVP просто удаляем
    from sqlalchemy import delete
    stmt = delete(MessageHistory).where(MessageHistory.user_id == user_id)
    await session.execute(stmt)
    await session.commit()

async def get_history_message_by_id(session: AsyncSession, msg_id: int):
    """Возвращает запись из истории по её ID (primary key)"""
    query = select(MessageHistory).where(MessageHistory.id == msg_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()    

async def start_generation_task(session: AsyncSession, user_id: int, cost: int):
    """Создает запись о начале генерации"""
    task = GenerationTask(user_id=user_id, cost=cost, status="processing")
    session.add(task)
    await session.commit()
    return task.id

async def finish_generation_task(session: AsyncSession, task_id: int, status: str = "completed"):
    """Помечает задачу как завершенную"""
    query = select(GenerationTask).where(GenerationTask.id == task_id)
    result = await session.execute(query)
    task = result.scalar_one_or_none()
    if task:
        task.status = status
        await session.commit()

async def refund_stuck_tasks(session: AsyncSession):
    """
    Находит все задачи, которые висят в 'processing' дольше 5 минут,
    возвращает бананы пользователям и помечает задачи как 'refunded'.
    """
    # Время отсечки (сейчас минус 5 минут)
    cutoff_time = datetime.now() - timedelta(minutes=5)
    
    # Ищем зависшие задачи
    query = select(GenerationTask).where(
        GenerationTask.status == "processing",
        GenerationTask.created_at < cutoff_time
    )
    result = await session.execute(query)
    stuck_tasks = result.scalars().all()
    
    refunded_count = 0
    refunded_bananas = 0
    
    for task in stuck_tasks:
        # Возвращаем бананы юзеру
        user_query = select(User).where(User.telegram_id == task.user_id)
        user_res = await session.execute(user_query)
        user = user_res.scalar_one_or_none()
        
        if user:
            user.generations_balance += task.cost
            task.status = "refunded"
            refunded_count += 1
            refunded_bananas += task.cost
            
    await session.commit()
    return refunded_count, refunded_bananas

async def get_user_model_preference(session: AsyncSession, user_id: int) -> str:
    """Возвращает 'standard' или 'pro'"""
    query = select(User.preferred_model).where(User.telegram_id == user_id)
    result = await session.execute(query)
    model = result.scalar_one_or_none()
    return model if model else "standard"

async def set_user_model_preference(session: AsyncSession, user_id: int, model: str):
    """Сохраняет выбор модели (sticky setting)"""
    # model должно быть 'standard' или 'pro'
    query = select(User).where(User.telegram_id == user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    if user:
        user.preferred_model = model
        await session.commit()

        # 👇 Добавь это в app/services/user_service.py

# Убедись, что в начале файла есть импорт select и User
# from sqlalchemy import select
# from app.models.user import User (или где у тебя лежит модель User)

async def get_user(session, telegram_id: int):
    """Просто ищет пользователя, возвращает None если не найден"""
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    return result.scalars().first()

# 👇 ЗАМЕНИ ФУНКЦИЮ create_user НА ЭТУ ВЕРСИЮ:

async def create_user(session, telegram_id: int, username: str, full_name: str, referrer_id: int = None, source: str = None):
    new_user = User(
        telegram_id=telegram_id, 
        username=username, 
        full_name=full_name,
        referrer_id=referrer_id,
        source=source, # ✅ ТЕПЕРЬ ЗАПИСЫВАЕМ ИСТОЧНИК
        generations_balance=0 
    )
    session.add(new_user)
    await session.commit()
    return new_user

# 👇 ДОБАВИТЬ В КОНЕЦ ФАЙЛА 👇

async def claim_subscription_bonus(session, user_id: int, bonus_type: str, amount: int) -> bool:
    """
    Выдает бонус за подписку, если еще не выдавали.
    bonus_type: 'channel' или 'chat'
    """
    user = await get_user(session, user_id) 
    if not user: return False

    if bonus_type == 'channel':
        if user.is_channel_sub_claimed: return False
        user.is_channel_sub_claimed = True
    
    elif bonus_type == 'chat':
        if user.is_chat_sub_claimed: return False
        user.is_chat_sub_claimed = True
    
    # Начисляем в оба поля
    user.generations_balance += amount
    user.balance_free += amount  # ← ДОБАВИЛИ
    await session.commit()
    return True

# 👇 ВСТАВИТЬ В КОНЕЦ ФАЙЛА user_service.py

async def get_user_financial_stats(session, user_id: int):
    """
    Возвращает статистику по деньгам юзера:
    - Кол-во покупок
    - Общая сумма трат
    - Источник трафика
    """
    # 1. Данные о юзере (источник)
    user = await get_user(session, user_id)
    source = user.source if user and user.source else "Органика"

    # 2. Считаем покупки
    # (func.count - количество, func.sum - сумма денег)
    stmt = select(func.count(Purchase.id), func.sum(Purchase.price)).where(Purchase.user_id == user_id, Purchase.status == "succeeded")
    result = await session.execute(stmt)
    count, total_spent = result.fetchone()

    return {
        "count": count or 0,
        "total_spent": total_spent or 0,
        "source": source
    }

async def get_user_admin_card_data(session: AsyncSession, user_id: int):
    """
    Собирает полную информацию о пользователе для админ-карточки
    """
    # 1. Основные данные пользователя
    user = await get_user(session, user_id)
    if not user:
        return None
    
    # 2. Статистика платежей (количество, сумма, последняя дата)
    stmt_payments = select(
        func.count(Purchase.id),
        func.sum(Purchase.price),
        func.max(Purchase.created_at)
    ).where(
        Purchase.user_id == user_id,
        Purchase.status == "succeeded"
    )
    result_payments = await session.execute(stmt_payments)
    payments_count, payments_sum, last_payment_date = result_payments.fetchone()
    
    # 3. Количество рефералов (сколько людей пригласил)
    stmt_referrals = select(func.count(User.id)).where(User.referrer_id == user_id)
    referrals_count = await session.scalar(stmt_referrals) or 0
    
    # 4. Вычисляем статус пользователя
    status = "Активен ✅" if not user.is_blocked else "Заблокировал бота ❌"
    
    # 5. Подсчитываем дни с момента регистрации
    from datetime import datetime, timezone
    days_with_us = (datetime.now(timezone.utc) - user.created_at.replace(tzinfo=timezone.utc)).days
    
    return {
        "user": user,
        "total_generations": user.total_generations_used,
        "last_generation_at": user.last_generation_at,
        "payments_count": payments_count or 0,
        "payments_sum": payments_sum or 0,
        "last_payment_date": last_payment_date,
        "source": user.source or "Прямой переход",
        "referrer_id": user.referrer_id,
        "referrals_count": referrals_count,
        "channel_bonus_claimed": user.is_channel_sub_claimed,
        "chat_bonus_claimed": user.is_chat_sub_claimed,
        "status": status,
        "days_with_us": days_with_us,
        "balance_free": user.balance_free,
        "balance_paid": user.balance_paid
    }