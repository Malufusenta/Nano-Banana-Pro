from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Purchase, User
from datetime import datetime # ✅ Добавил, чтобы работало время
from sqlalchemy import select, update, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Purchase, User, BananaTransaction
from datetime import datetime

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
        purchase.completed_at = datetime.now()
    else:
        # 3. Если не нашли (быстрая оплата) — СОЗДАЕМ новую
        purchase = Purchase(
            user_id=user_id,
            price=price,
            amount=0, 
            status="succeeded",
            created_at=datetime.now(),
            completed_at=datetime.now()
        )
        session.add(purchase)

    # Commit будет в вызывающей функции
    return True

async def update_purchase_analytics(
    session: AsyncSession, 
    user_id: int, 
    price: float,
    tariff_name: str,
    payment_id: str = None,
    income_amount: float = None,
    payment_method: str = None
):
    """
    Обновляет аналитику после успешной покупки:
    1. Заполняет детали Purchase
    2. Обновляет LTV метрики User
    3. Проверяет флаг "копил или сразу купил" при первой покупке
    """
    # 1. Получаем юзера
    user_result = await session.execute(
        select(User).where(User.telegram_id == user_id)
    )
    user = user_result.scalar_one_or_none()
    
    if not user:
        return
    
    # 2. Находим Purchase и заполняем детали
    purchase_result = await session.execute(
        select(Purchase)
        .where(Purchase.user_id == user_id, Purchase.price == price, Purchase.status == "succeeded")
        .order_by(desc(Purchase.created_at))
        .limit(1)
    )
    purchase = purchase_result.scalar_one_or_none()
    
    if purchase:
        purchase.tariff_name = tariff_name
        purchase.user_source = user.source or "organic"
        purchase.completed_at = datetime.now()
        if payment_id:
            purchase.payment_id = payment_id

        if income_amount:
            purchase.income_amount = int(income_amount * 100)
        if payment_method:
            purchase.payment_method = payment_method
        prev_count = await session.scalar(
            select(func.count()).where(
                Purchase.user_id == user_id,
                Purchase.status == "succeeded",
                Purchase.id != purchase.id,
                Purchase.completed_at < purchase.completed_at
            )
        )
        purchase.is_first_purchase = (prev_count == 0)
    
    # 3. Обновляем LTV метрики юзера
    user.total_revenue += int(price)
    user.orders_count += 1
    
    # 4. Проверяем первую покупку
    if user.orders_count == 1:  # Это первая покупка
        user.first_purchase_at = datetime.now()
        
        # Проверяем: были ли бесплатные начисления ДО покупки?
        free_transactions_result = await session.execute(
            select(BananaTransaction)
            .where(
                BananaTransaction.user_id == user_id,
                BananaTransaction.transaction_type.in_(["earned_ref", "earned_sub"])
            )
            .limit(1)
        )
        had_free_actions = free_transactions_result.scalar_one_or_none() is not None
        user.had_free_actions_before_purchase = had_free_actions
    
    # Commit будет в вызывающей функции