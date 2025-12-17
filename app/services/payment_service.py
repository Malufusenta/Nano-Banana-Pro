from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Purchase, User

async def create_purchase_record(session: AsyncSession, user_id: int, price: int, gens_amount: int):
    """Создает запись о том, что человек хочет купить (статус pending)"""
    purchase = Purchase(
        user_id=user_id,
        price=price,
        amount=gens_amount,
        status="pending"
    )
    session.add(purchase)
    await session.commit()
    return purchase

async def confirm_purchase(session: AsyncSession, purchase_id: int) -> bool:
    """
    Подтверждает оплату заказа:
    1. Меняет статус покупки на 'paid'
    2. Начисляет генерации пользователю
    """
    # 1. Ищем заказ по ID
    query = select(Purchase).where(Purchase.id == purchase_id)
    result = await session.execute(query)
    purchase = result.scalar_one_or_none()

    if not purchase or purchase.status == "paid":
        return False # Заказ не найден или уже оплачен

    # 2. Меняем статус
    purchase.status = "paid"

    # 3. Начисляем баланс юзеру
    user_query = select(User).where(User.telegram_id == purchase.user_id)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one_or_none()
    
    if user:
        user.generations_balance += purchase.amount
        # Обновляем статистику трат юзера? (можно добавить поле total_spent в User, если нужно)
    
    await session.commit()
    return True

async def fulfill_payment(session: AsyncSession, user_id: int, gens_amount: int):
    """
    Начисляет генерации пользователю.
    Используется после успешной оплаты или админом.
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

# 👇 ВСТАВИТЬ В КОНЕЦ ФАЙЛА app/services/payment_service.py

async def mark_purchase_as_succeeded(session, user_id: int, price: int):
    """
    Находит последнюю запись 'pending' с такой ценой и меняет статус на 'succeeded'
    """
    # 1. Ищем последнюю неоплаченную покупку этого юзера на эту сумму
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
        await session.commit()
        return True
    return False