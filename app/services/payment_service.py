from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Purchase, User
from datetime import datetime # ✅ Добавил, чтобы работало время

async def create_purchase_record(session: AsyncSession, user_id: int, price: int, gens_amount: int):
    """Создает запись о том, что человек хочет купить (статус pending)"""
    purchase = Purchase(
        user_id=user_id,
        price=price,
        amount=gens_amount,
        status="pending",
        created_at=datetime.now() # ✅ Фиксируем дату создания
    )
    session.add(purchase)
    await session.commit()
    return purchase

async def confirm_purchase(session: AsyncSession, purchase_id: int) -> bool:
    """
    Подтверждает оплату заказа (для кнопок или админки):
    1. Меняет статус покупки на 'succeeded'
    2. Начисляет генерации пользователю
    """
    # 1. Ищем заказ по ID
    query = select(Purchase).where(Purchase.id == purchase_id)
    result = await session.execute(query)
    purchase = result.scalar_one_or_none()

    if not purchase or purchase.status == "succeeded":
        return False # Заказ не найден или уже оплачен

    # 2. Меняем статус
    purchase.status = "succeeded"

    # 3. Начисляем баланс юзеру
    user_query = select(User).where(User.telegram_id == purchase.user_id)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one_or_none()
    
    if user:
        user.generations_balance += purchase.amount
        user.balance_paid += purchase.amount 
    
    await session.commit()
    return True

async def fulfill_payment(session: AsyncSession, user_id: int, gens_amount: int):
    """
    Начисляет генерации пользователю (ручное пополнение).
    """
    # 1. Находим юзера
    query = select(User).where(User.telegram_id == user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    if user:
        # 2. Добавляем генерации
        user.generations_balance += gens_amount
        await session.commit()
        return user.generations_balance
    return None

# 👇 ВОТ ЭТА ФУНКЦИЯ ТЕПЕРЬ УМНАЯ 👇

async def mark_purchase_as_succeeded(session: AsyncSession, user_id: int, price: float):
    """
    Фиксирует успешную оплату для вебхука.
    Логика:
    1. Ищет 'pending' запись.
    2. Если находит -> меняет статус на 'succeeded'.
    3. Если НЕ находит -> создает НОВУЮ запись 'succeeded'.
    """
    # 1. Ищем последнюю неоплаченную покупку
    stmt = select(Purchase).where(
        Purchase.user_id == user_id,
        Purchase.price == price,
        Purchase.status == "pending"
    ).order_by(desc(Purchase.created_at)).limit(1)
    
    result = await session.execute(stmt)
    purchase = result.scalars().first()
    
    # 2. Если нашли — обновляем статус
    if purchase:
        purchase.status = "succeeded"
    else:
        # 3. Если не нашли (быстрая оплата) — СОЗДАЕМ новую
        # (amount ставим 0, так как бананы начисляются отдельно в webhook_server)
        purchase = Purchase(
            user_id=user_id,
            price=price,
            amount=0, 
            status="succeeded",
            created_at=datetime.now()
        )
        session.add(purchase)

    await session.commit()
    return True