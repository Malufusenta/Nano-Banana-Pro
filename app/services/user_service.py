from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Purchase
from datetime import datetime, timedelta, timezone
from app.config import START_BALANCE
from app.models import MessageHistory, GenerationTask

# --- ТВОЯ СТАРАЯ ЛОГИКА ---

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
        generations_balance=START_BALANCE
    )
    session.add(new_user)
    await session.commit()
    return new_user, True

async def check_and_deduct_balance(session: AsyncSession, telegram_id: int, amount: int = 1) -> bool:
    """
    Списывает указанное количество генераций (amount).
    """
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        return False
    
    total_balance = user.balance_paid + user.balance_free
    if total_balance < amount:
        return False  # Недостаточно бананов
    
    remaining = amount
    
    if user.balance_paid > 0:
        deduct_from_paid = min(user.balance_paid, remaining)
        user.balance_paid -= deduct_from_paid
        remaining -= deduct_from_paid
    
    if remaining > 0:
        user.balance_free -= remaining
    
    user.generations_balance = user.balance_paid + user.balance_free
    user.total_generations_used += 1
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
        user.balance_free += amount
        user.is_sub_bonus_claimed = True
        await session.commit()
        return True
    return False

# --- СТАТИСТИКА (ТУТ Я ВНЕС ПРАВКУ ДЛЯ ПЛАТЕЖЕЙ) ---

async def get_bot_stats(session: AsyncSession):
    users_count = await session.scalar(select(func.count(User.id)))
    gens_count = await session.scalar(select(func.sum(User.total_generations_used))) or 0
    # ✅ ПРАВКА: Считаем и 'paid' и 'succeeded'
    money = await session.scalar(select(func.sum(Purchase.price)).where(Purchase.status.in_(['succeeded', 'paid']))) or 0
    
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

    # ✅ ПРАВКА: Считаем и 'paid' и 'succeeded'
    query_spent = select(func.sum(Purchase.price)).where(Purchase.user_id == user_id, Purchase.status.in_(['succeeded', 'paid']))
    total_spent = await session.scalar(query_spent) or 0

    query_purchases = (
        select(Purchase)
        .where(Purchase.user_id == user_id, Purchase.status.in_(['succeeded', 'paid']))
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

async def is_user_premium(session: AsyncSession, user_id: int) -> bool:
    """Проверяет, были ли у пользователя оплаченные покупки"""
    # ✅ ПРАВКА: Считаем и 'paid' и 'succeeded'
    query = select(Purchase).where(Purchase.user_id == user_id, Purchase.status.in_(['succeeded', 'paid']))
    result = await session.execute(query)
    return result.first() is not None

# --- ПОИСК И АДМИНКА ---

async def find_user_by_input(session: AsyncSession, user_input: str):
    """
    Универсальный поиск: ищет совпадение ИЛИ по ID, ИЛИ по Username.
    """
    clean_input = str(user_input).strip().replace("@", "")
    
    conditions = []
    conditions.append(User.username == clean_input)
    
    if clean_input.isdigit():
        conditions.append(User.telegram_id == int(clean_input))
    
    query = select(User).where(or_(*conditions))
    
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    return user

async def admin_change_balance(session: AsyncSession, user_id: int, amount: int):
    """
    Меняет баланс пользователя.
    При начислении (+) - добавляет в balance_free
    При снятии (-) - сначала из balance_free, потом из balance_paid
    """
    query = select(User).where(User.telegram_id == user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        return None
    
    # НАЧИСЛЕНИЕ (положительное число)
    if amount > 0:
        user.balance_free += amount
    
    # СНЯТИЕ (отрицательное число)
    else:
        to_remove = abs(amount)
        
        # Сначала вычитаем из бесплатных
        if user.balance_free > 0:
            deduct_from_free = min(user.balance_free, to_remove)
            user.balance_free -= deduct_from_free
            to_remove -= deduct_from_free
        
        # Если осталось что снимать - вычитаем из платных
        if to_remove > 0 and user.balance_paid > 0:
            deduct_from_paid = min(user.balance_paid, to_remove)
            user.balance_paid -= deduct_from_paid
            to_remove -= deduct_from_paid
    
    # Обновляем общий баланс
    user.generations_balance = user.balance_paid + user.balance_free
    
    await session.commit()
    return user.generations_balance

# --- ИСТОРИЯ ---

async def add_history(session: AsyncSession, user_id: int, role: str, content: str, has_image: bool = False, file_id: str = None, image_url: str = None):
    msg = MessageHistory(user_id=user_id, role=role, content=content, has_image=has_image, file_id=file_id, image_url=image_url)
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg

async def get_dialog_context(session: AsyncSession, user_id: int, limit: int = 6):
    query = (
        select(MessageHistory)
        .where(MessageHistory.user_id == user_id)
        .order_by(desc(MessageHistory.created_at))
        .limit(limit)
    )
    result = await session.execute(query)
    return result.scalars().all()[::-1]

async def clear_history(session: AsyncSession, user_id: int):
    from sqlalchemy import delete
    stmt = delete(MessageHistory).where(MessageHistory.user_id == user_id)
    await session.execute(stmt)
    await session.commit()

async def get_history_message_by_id(session: AsyncSession, msg_id: int):
    query = select(MessageHistory).where(MessageHistory.id == msg_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()    

# --- ЗАДАЧИ ---

async def start_generation_task(session: AsyncSession, user_id: int, cost: int):
    task = GenerationTask(user_id=user_id, cost=cost, status="processing")
    session.add(task)
    await session.commit()
    return task.id

async def finish_generation_task(session: AsyncSession, task_id: int, status: str = "completed"):
    query = select(GenerationTask).where(GenerationTask.id == task_id)
    result = await session.execute(query)
    task = result.scalar_one_or_none()
    if task:
        task.status = status
        await session.commit()

async def refund_stuck_tasks(session: AsyncSession):
    cutoff_time = datetime.now() - timedelta(minutes=5)
    query = select(GenerationTask).where(
        GenerationTask.status == "processing",
        GenerationTask.created_at < cutoff_time
    )
    result = await session.execute(query)
    stuck_tasks = result.scalars().all()
    
    refunded_count = 0
    refunded_bananas = 0
    
    for task in stuck_tasks:
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

# --- МОДЕЛИ ---

async def get_user_model_preference(session: AsyncSession, user_id: int) -> str:
    query = select(User.preferred_model).where(User.telegram_id == user_id)
    result = await session.execute(query)
    model = result.scalar_one_or_none()
    return model if model else "standard"

async def set_user_model_preference(session: AsyncSession, user_id: int, model: str):
    query = select(User).where(User.telegram_id == user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    if user:
        user.preferred_model = model
        await session.commit()

# --- ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ (КОТОРЫЕ БЫЛИ У ТЕБЯ В КОНЦЕ) ---

async def get_user(session, telegram_id: int):
    """Просто ищет пользователя, возвращает None если не найден"""
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    return result.scalars().first()

# ✅ ПРАВКА: Заменил функцию создания (добавил аргументы referrer_id и source)
async def create_user(session, telegram_id: int, username: str, full_name: str, referrer_id: int = None, source: str = None):
    new_user = User(
        telegram_id=telegram_id, 
        username=username, 
        full_name=full_name,
        referrer_id=referrer_id,
        source=source, # ✅ Пишем источник
        generations_balance=0,
        balance_free=0, # Добавил дефолт
        balance_paid=0  # Добавил дефолт
    )
    session.add(new_user)
    await session.commit()
    return new_user

async def get_user_financial_stats(session, user_id: int):
    """Для логов оплаты"""
    user = await get_user(session, user_id)
    source = user.source if user and user.source else "Органика"

    # ✅ ПРАВКА: Считаем и 'paid' и 'succeeded'
    stmt = select(func.count(Purchase.id), func.sum(Purchase.price)).where(
        Purchase.user_id == user_id, 
        Purchase.status.in_(['succeeded', 'paid'])
    )
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
    user = await get_user(session, user_id)
    if not user:
        return None
    
    # ✅ ПРАВКА: Считаем и 'paid' и 'succeeded'
    stmt_payments = select(
        func.count(Purchase.id),
        func.sum(Purchase.price),
        func.max(Purchase.created_at)
    ).where(
        Purchase.user_id == user_id,
        Purchase.status.in_(['succeeded', 'paid'])
    )
    result_payments = await session.execute(stmt_payments)
    payments_count, payments_sum, last_payment_date = result_payments.fetchone()
    
    stmt_referrals = select(func.count(User.id)).where(User.referrer_id == user_id)
    referrals_count = await session.scalar(stmt_referrals) or 0
    
    is_blocked = getattr(user, 'is_blocked', False)
    status = "Активен ✅" if not is_blocked else "Заблокировал бота ❌"
    
    # Защита от timezone ошибок
    try:
        days_with_us = (datetime.now(timezone.utc) - user.created_at.replace(tzinfo=timezone.utc)).days
    except:
        days_with_us = (datetime.now() - user.created_at).days
    
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
        "channel_bonus_claimed": getattr(user, 'is_channel_sub_claimed', False),
        "chat_bonus_claimed": getattr(user, 'is_chat_sub_claimed', False),
        "status": status,
        "days_with_us": days_with_us,
        "balance_free": user.balance_free,
        "balance_paid": user.balance_paid
    }

async def add_paid_balance(session: AsyncSession, user_id: int, amount: int):
    """
    Начисляет ПЛАТНЫЕ бананы (после покупки).
    """
    query = select(User).where(User.telegram_id == user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    
    if user:
        user.balance_paid += amount
        user.generations_balance = user.balance_paid + user.balance_free
        await session.commit()
        return user.generations_balance
    return None

async def has_user_purchased(session: AsyncSession, user_id: int) -> bool:
    """Проверяет, были ли у пользователя успешные покупки"""
    query = select(Purchase).where(
        Purchase.user_id == user_id, 
        Purchase.status.in_(['succeeded', 'paid'])
    ).limit(1)
    result = await session.execute(query)
    return result.first() is not None