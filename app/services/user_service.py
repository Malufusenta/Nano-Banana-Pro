from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Purchase, MessageHistory, GenerationTask
from datetime import datetime, timedelta
from app.config import START_BALANCE

# --- ПОЛЬЗОВАТЕЛИ ---

async def get_user(session: AsyncSession, telegram_id: int):
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    return result.scalars().first()

async def create_user(session: AsyncSession, telegram_id: int, username: str, full_name: str, referrer_id: int = None, source: str = None):
    new_user = User(
        telegram_id=telegram_id, 
        username=username, 
        full_name=full_name,
        referrer_id=referrer_id,
        source=source,
        generations_balance=0,
        balance_free=0,
        balance_paid=0
    )
    session.add(new_user)
    await session.commit()
    return new_user

async def find_user_by_input(session: AsyncSession, user_input: str):
    clean_input = str(user_input).strip().replace("@", "")
    conditions = [User.username == clean_input]
    if clean_input.isdigit():
        conditions.append(User.telegram_id == int(clean_input))
    query = select(User).where(or_(*conditions))
    return (await session.execute(query)).scalar_one_or_none()

# --- БАЛАНС ---

async def get_user_balance(session: AsyncSession, telegram_id: int) -> int:
    user = await get_user(session, telegram_id)
    return user.generations_balance if user else 0

async def add_paid_balance(session: AsyncSession, user_id: int, amount: int):
    user = await get_user(session, user_id)
    if user:
        user.balance_paid += amount
        user.generations_balance = user.balance_paid + user.balance_free
        await session.commit()
        return user.generations_balance

async def admin_change_balance(session: AsyncSession, user_id: int, amount: int):
    user = await get_user(session, user_id)
    if user:
        user.balance_free += amount
        if user.balance_free < 0: user.balance_free = 0
        user.generations_balance = user.balance_paid + user.balance_free
        await session.commit()
        return user.generations_balance

async def check_and_deduct_balance(session: AsyncSession, telegram_id: int, amount: int = 1) -> bool:
    user = await get_user(session, telegram_id)
    if not user: return False
    
    total = user.balance_paid + user.balance_free
    if total < amount: return False
    
    remaining = amount
    if user.balance_paid > 0:
        deducted = min(user.balance_paid, remaining)
        user.balance_paid -= deducted
        remaining -= deducted
    if remaining > 0:
        user.balance_free -= remaining
        
    user.generations_balance = user.balance_paid + user.balance_free
    user.total_generations_used += 1
    user.last_generation_at = datetime.now()
    await session.commit()
    return True

# --- СТАТИСТИКА (ГЛАВНОЕ ИСПРАВЛЕНИЕ ТУТ) ---

async def get_user_financial_stats(session: AsyncSession, user_id: int):
    user = await get_user(session, user_id)
    source = user.source if user and user.source else "Органика"

    # 🔥 ИЩЕМ И 'succeeded' И 'paid' (чтобы видеть и старые, и новые покупки)
    stmt = select(func.count(Purchase.id), func.sum(Purchase.price)).where(
        Purchase.user_id == user_id, 
        Purchase.status.in_(['succeeded', 'paid']) # <--- ВОТ ФИКС
    )
    result = await session.execute(stmt)
    count, total_spent = result.fetchone()

    return {
        "count": count or 0,
        "total_spent": total_spent or 0,
        "source": source
    }

async def get_user_profile_data(session: AsyncSession, user_id: int):
    user = await get_user(session, user_id)
    if not user: return None
    # Тут тоже фиксим фильтр
    q_spent = select(func.sum(Purchase.price)).where(Purchase.user_id == user_id, Purchase.status.in_(['succeeded', 'paid']))
    return {"user": user, "total_spent": await session.scalar(q_spent) or 0}

async def get_user_admin_card_data(session: AsyncSession, user_id: int):
    user = await get_user(session, user_id)
    if not user: return None
    
    stmt_payments = select(func.count(Purchase.id), func.sum(Purchase.price)).where(Purchase.user_id == user_id, Purchase.status.in_(['succeeded', 'paid']))
    p_count, p_sum = (await session.execute(stmt_payments)).fetchone()
    ref_count = await session.scalar(select(func.count(User.id)).where(User.referrer_id == user_id)) or 0
    
    return {
        "user": user,
        "total_generations": user.total_generations_used,
        "payments_count": p_count or 0,
        "payments_sum": p_sum or 0,
        "source": user.source or "Органика",
        "referrals_count": ref_count,
        "balance_free": user.balance_free,
        "balance_paid": user.balance_paid
    }

async def get_bot_stats(session: AsyncSession):
    users = await session.scalar(select(func.count(User.id)))
    gens = await session.scalar(select(func.sum(User.total_generations_used))) or 0
    money = await session.scalar(select(func.sum(Purchase.price)).where(Purchase.status.in_(['succeeded', 'paid']))) or 0
    return {"users": users, "gens": gens, "money": money}

# --- ИСТОРИЯ И ДОПЫ ---

async def add_history(session: AsyncSession, user_id: int, role: str, content: str, has_image: bool = False, file_id: str = None, image_url: str = None):
    msg = MessageHistory(user_id=user_id, role=role, content=content, has_image=has_image, file_id=file_id, image_url=image_url)
    session.add(msg)
    await session.commit()
    return msg

async def get_dialog_context(session: AsyncSession, user_id: int, limit: int = 6):
    query = select(MessageHistory).where(MessageHistory.user_id == user_id).order_by(desc(MessageHistory.created_at)).limit(limit)
    return (await session.execute(query)).scalars().all()[::-1]

async def clear_history(session: AsyncSession, user_id: int):
    from sqlalchemy import delete
    await session.execute(delete(MessageHistory).where(MessageHistory.user_id == user_id))
    await session.commit()

async def start_generation_task(session: AsyncSession, user_id: int, cost: int):
    task = GenerationTask(user_id=user_id, cost=cost, status="processing")
    session.add(task)
    await session.commit()
    return task.id

async def finish_generation_task(session: AsyncSession, task_id: int, status: str = "completed"):
    task = (await session.execute(select(GenerationTask).where(GenerationTask.id == task_id))).scalar_one_or_none()
    if task:
        task.status = status
        await session.commit()

async def refund_stuck_tasks(session: AsyncSession):
    cutoff = datetime.now() - timedelta(minutes=5)
    tasks = (await session.execute(select(GenerationTask).where(GenerationTask.status == "processing", GenerationTask.created_at < cutoff))).scalars().all()
    refunded = 0
    for task in tasks:
        user = await get_user(session, task.user_id)
        if user:
            user.balance_free += task.cost
            user.generations_balance = user.balance_free + user.balance_paid
            task.status = "refunded"
            refunded += 1
    await session.commit()
    return refunded

async def get_user_model_preference(session: AsyncSession, user_id: int) -> str:
    user = await get_user(session, user_id)
    return user.preferred_model if user else "standard"

async def set_user_model_preference(session: AsyncSession, user_id: int, model: str):
    user = await get_user(session, user_id)
    if user:
        user.preferred_model = model
        await session.commit()

async def is_user_premium(session: AsyncSession, user_id: int) -> bool:
    query = select(Purchase).where(Purchase.user_id == user_id, Purchase.status.in_(['succeeded', 'paid']))
    result = await session.execute(query)
    return result.first() is not None

async def get_history_message_by_id(session: AsyncSession, msg_id: int):
    query = select(MessageHistory).where(MessageHistory.id == msg_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()

async def claim_bonus(session: AsyncSession, user_id: int, amount: int = 5) -> bool:
    user = await get_user(session, user_id)
    if user and not user.is_sub_bonus_claimed:
        user.balance_free += amount
        user.generations_balance = user.balance_free + user.balance_paid
        user.is_sub_bonus_claimed = True
        await session.commit()
        return True
    return False