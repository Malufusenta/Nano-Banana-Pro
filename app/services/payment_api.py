import uuid
from yookassa import Configuration, Payment
from app import config

Configuration.account_id = config.YOO_SHOP_ID
Configuration.secret_key = config.YOO_SECRET_KEY

def create_yoo_payment(amount, description, user_id, payment_method=None):
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
            "user_id": user_id
        }
    }

    # 👇 Если указан конкретный способ, добавляем его в запрос
    if payment_method:
        payment_data["payment_method_data"] = {
            "type": payment_method
        }

    payment = Payment.create(payment_data, idempotence_key)
    return payment

def check_yoo_payment(payment_id):
    payment = Payment.find_one(payment_id)
    return payment.status