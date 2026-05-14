import uuid
from decimal import Decimal

from yookassa import Configuration, Payment

from app import config

Configuration.account_id = config.YOO_SHOP_ID
Configuration.secret_key = config.YOO_SECRET_KEY


def _normalize_yoo_amount(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def create_yoo_payment(amount, description, user_id, purchase_id, payment_method=None):
    """
    payment_method: 'bank_card', 'sbp', или None (по умолчанию выбор на странице ЮКассы)
    """
    idempotence_key = str(uuid.uuid4())
    
    # Базовые данные
    payment_data = {
        "amount": {
            "value": str(amount),
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://t.me/nanobanan_bot" 
        },
        "capture": True,
        "description": description,
        "metadata": {
            "user_id": str(user_id),
            "purchase_id": str(purchase_id),
        }
    }

    # 👇 Если указан конкретный способ, добавляем его в запрос
    if payment_method:
        payment_data["payment_method_data"] = {
            "type": payment_method
        }

    payment = Payment.create(payment_data, idempotence_key)
    return payment


def get_yoo_payment_details(payment_id):
    payment = Payment.find_one(payment_id)
    amount = getattr(payment, "amount", None)
    income_amount = getattr(payment, "income_amount", None)
    payment_method = getattr(payment, "payment_method", None)
    metadata = getattr(payment, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = dict(metadata)

    return {
        "id": getattr(payment, "id", payment_id),
        "status": getattr(payment, "status", None),
        "amount": _normalize_yoo_amount(getattr(amount, "value", None)),
        "income_amount": _normalize_yoo_amount(getattr(income_amount, "value", None)),
        "captured_at": getattr(payment, "captured_at", None),
        "payment_method": getattr(payment_method, "type", None),
        "metadata": metadata,
        "raw": payment,
    }


def check_yoo_payment(payment_id):
    return get_yoo_payment_details(payment_id)["status"]