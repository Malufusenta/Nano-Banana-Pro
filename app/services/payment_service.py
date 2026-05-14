from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdScenario, BananaTransaction, PaymentAttempt, Purchase, User
from app.services.user_service import add_paid_balance


@dataclass(slots=True)
class YookassaFinalizeResult:
    status: str
    purchase_id: int | None = None
    user_id: int | None = None
    amount: int = 0
    price: int = 0
    tariff_name: str | None = None
    is_first_purchase: bool = False
    scenario_incremented: bool = False


def _normalize_money(value: Decimal | float | int | str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _money_to_cents(value: Decimal | float | int | str | None) -> int | None:
    normalized = _normalize_money(value)
    if normalized is None:
        return None
    return int((normalized * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _resolve_last_payment_method(payment_method: str | None) -> str:
    if payment_method == "bank_card":
        return "yookassa_card"
    if payment_method == "sbp":
        return "yookassa_sbp"
    return f"yookassa_{payment_method or 'unknown'}"

async def create_purchase_record(session: AsyncSession, user_id: int, price: int, gens_amount: int):
    """Создает запись о том, что человек хочет купить (статус pending)"""
    purchase = Purchase(
        user_id=user_id,
        price=price,
        amount=gens_amount,
        status="pending",
        created_at=datetime.now(),
    )
    session.add(purchase)
    await session.flush()
    return purchase


async def create_payment_attempt_record(
    session: AsyncSession,
    purchase_id: int,
    payment_id: str,
    payment_method: str | None,
) -> PaymentAttempt:
    attempt = PaymentAttempt(
        purchase_id=purchase_id,
        payment_id=payment_id,
        payment_method=payment_method,
        status="pending",
    )
    session.add(attempt)
    await session.flush()
    return attempt


async def get_latest_purchase_attempt(
    session: AsyncSession,
    purchase_id: int,
) -> PaymentAttempt | None:
    return await session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.purchase_id == purchase_id)
        .order_by(PaymentAttempt.created_at.desc(), PaymentAttempt.id.desc())
        .limit(1)
    )


async def finalize_yookassa_purchase(
    session: AsyncSession,
    *,
    payment_id: str,
    amount: Decimal | float | int | str,
    payment_method: str | None,
    income_amount: Decimal | float | int | str | None,
    completed_at: datetime | None,
    tariff_name: str,
) -> YookassaFinalizeResult:
    attempt = await session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.payment_id == payment_id)
        .limit(1)
    )
    if not attempt:
        return YookassaFinalizeResult(status="not_found")

    purchase = await session.scalar(
        select(Purchase)
        .where(Purchase.id == attempt.purchase_id)
        .with_for_update()
    )
    if not purchase:
        return YookassaFinalizeResult(status="not_found", purchase_id=attempt.purchase_id)

    user = await session.scalar(
        select(User)
        .where(User.telegram_id == purchase.user_id)
        .with_for_update()
    )
    if not user:
        return YookassaFinalizeResult(status="not_found", purchase_id=purchase.id, user_id=purchase.user_id)

    provider_amount = _normalize_money(amount)
    expected_amount = _normalize_money(purchase.price)
    if provider_amount != expected_amount:
        return YookassaFinalizeResult(
            status="amount_mismatch",
            purchase_id=purchase.id,
            user_id=purchase.user_id,
            amount=purchase.amount,
            price=purchase.price,
            tariff_name=tariff_name,
        )

    if purchase.status == "succeeded":
        if purchase.payment_id == payment_id:
            if attempt.status != "succeeded":
                attempt.status = "succeeded"
            return YookassaFinalizeResult(
                status="already_processed",
                purchase_id=purchase.id,
                user_id=purchase.user_id,
                amount=purchase.amount,
                price=purchase.price,
                tariff_name=purchase.tariff_name or tariff_name,
                is_first_purchase=bool(purchase.is_first_purchase),
            )
        if attempt.status == "pending":
            attempt.status = "canceled"
        return YookassaFinalizeResult(
            status="duplicate_ignored",
            purchase_id=purchase.id,
            user_id=purchase.user_id,
            amount=purchase.amount,
            price=purchase.price,
            tariff_name=purchase.tariff_name or tariff_name,
            is_first_purchase=bool(purchase.is_first_purchase),
        )

    if purchase.status != "pending":
        return YookassaFinalizeResult(
            status="conflict",
            purchase_id=purchase.id,
            user_id=purchase.user_id,
            amount=purchase.amount,
            price=purchase.price,
            tariff_name=tariff_name,
        )

    attempt.payment_method = payment_method or attempt.payment_method
    attempt.status = "succeeded"
    purchase.payment_id = payment_id
    purchase.status = "succeeded"
    purchase.completed_at = completed_at or datetime.utcnow()
    purchase.tariff_name = tariff_name
    purchase.payment_method = attempt.payment_method
    purchase.income_amount = _money_to_cents(income_amount)

    first_source = await session.scalar(
        select(Purchase.user_source)
        .where(
            Purchase.user_id == purchase.user_id,
            Purchase.status == "succeeded",
            Purchase.user_source.isnot(None),
            Purchase.id != purchase.id,
        )
        .order_by(Purchase.completed_at)
        .limit(1)
    )
    purchase.user_source = first_source or user.source or "organic"

    previous_purchase_count = await session.scalar(
        select(func.count(Purchase.id)).where(
            Purchase.user_id == purchase.user_id,
            Purchase.status == "succeeded",
            Purchase.id != purchase.id,
        )
    )
    is_first_purchase = previous_purchase_count == 0
    purchase.is_first_purchase = is_first_purchase

    await add_paid_balance(session, purchase.user_id, purchase.amount)
    user.total_revenue += purchase.price
    user.orders_count += 1
    user.last_payment_method = _resolve_last_payment_method(attempt.payment_method)

    if is_first_purchase:
        user.first_purchase_at = purchase.completed_at
        had_free_actions = await session.scalar(
            select(BananaTransaction.id)
            .where(
                BananaTransaction.user_id == purchase.user_id,
                BananaTransaction.transaction_type.in_(["earned_ref", "earned_sub"]),
            )
            .limit(1)
        )
        user.had_free_actions_before_purchase = had_free_actions is not None

    scenario_incremented = False
    if user.active_scenario_id:
        await session.execute(
            update(AdScenario)
            .where(AdScenario.id == user.active_scenario_id)
            .values(total_purchases=AdScenario.total_purchases + 1)
        )
        scenario_incremented = True

    await session.execute(
        update(PaymentAttempt)
        .where(
            PaymentAttempt.purchase_id == purchase.id,
            PaymentAttempt.id != attempt.id,
            PaymentAttempt.status == "pending",
        )
        .values(status="canceled")
    )

    return YookassaFinalizeResult(
        status="applied",
        purchase_id=purchase.id,
        user_id=purchase.user_id,
        amount=purchase.amount,
        price=purchase.price,
        tariff_name=tariff_name,
        is_first_purchase=is_first_purchase,
        scenario_incremented=scenario_incremented,
    )

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

async def mark_purchase_as_succeeded(
    session: AsyncSession,
    user_id: int,
    price: float,
    gens_amount: int = 0,
    completed_at: datetime = None,
):
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
    ).order_by(Purchase.created_at.desc()).limit(1)
    
    result = await session.execute(stmt)
    purchase = result.scalars().first()
    
    # Используем время из параметра или текущее UTC
    final_completed_at = completed_at if completed_at else datetime.utcnow()
    
    # 2. Если нашли — обновляем статус
    if purchase:
        purchase.status = "succeeded"
        purchase.completed_at = final_completed_at
    else:
        # 3. Если не нашли (быстрая оплата) — СОЗДАЕМ новую
        purchase = Purchase(
            user_id=user_id,
            price=price,
            amount=gens_amount,
            status="succeeded",
            created_at=datetime.now(),
            completed_at=final_completed_at
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
        .order_by(Purchase.created_at.desc())
        .limit(1)
    )
    purchase = purchase_result.scalar_one_or_none()
    
    if purchase:
        purchase.tariff_name = tariff_name

        # Берём source из первой успешной покупки чтобы не менять атрибуцию
        # Если покупок ещё не было — используем текущий user.source
        first_purchase_result = await session.execute(
            select(Purchase.user_source)
            .where(
                Purchase.user_id == user_id,
                Purchase.status == "succeeded",
                Purchase.user_source.isnot(None),
                Purchase.id != purchase.id
            )
            .order_by(Purchase.completed_at)
            .limit(1)
        )
        first_source = first_purchase_result.scalar_one_or_none()
        purchase.user_source = first_source or user.source or "organic"
        # Не трогаем completed_at - оно уже установлено из ЮКассы в mark_purchase_as_succeeded
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