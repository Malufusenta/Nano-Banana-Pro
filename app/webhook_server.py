from aiohttp import web
from app.database import async_session
from app.services.user_service import add_paid_balance
from app.packages import PACKAGES 

# 👇 ИМПОРТИРУЕМ ТВОЙ ЛОГГЕР
# (Если файл лежит не в app/utils/logger.py, поправь путь)
from app.services.admin_logger import log_payment

def get_banana_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11: return "банан"
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20): return "банана"
    return "бананов"

def get_bananas_by_price(price):
    target_price = float(price)
    for key, pkg in PACKAGES.items():
        if float(pkg['price']) == target_price:
            return pkg['gens']
    return 0

async def handle_yookassa_webhook(request):
    bot = request.app['bot']
    try:
        event_json = await request.json()
    except Exception:
        return web.Response(status=200)

    if event_json.get('event') == 'payment.succeeded':
        payment_object = event_json['object']
        metadata = payment_object.get('metadata', {})
        user_id = int(metadata.get('user_id', 0))
        amount = float(payment_object['amount']['value'])
        
        if user_id == 0:
            return web.Response(status=200)

        bananas = get_bananas_by_price(amount)
        
        if bananas > 0:
            async with async_session() as session:
                await add_paid_balance(session, user_id, bananas)
            
            # 1. Отправляем сообщение юзеру
            try:
                suffix = get_banana_word(bananas)
                text = (
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"🍌 Начислено: <b>+{bananas} {suffix}</b>\n"
                    f"Спасибо за покупку! Можно снова творить 🎨"
                )
                await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
            except Exception as e:
                print(f"⚠️ Не удалось отправить сообщение юзеру: {e}")

            # 2. 👇 ОТПРАВЛЯЕМ ЛОГ АДМИНУ
            try:
                # Получаем инфо о юзере из Телеграма (чтобы узнать username)
                user_info = await bot.get_chat(user_id)
                
                # Формируем название товара
                item_name = f"{bananas} {get_banana_word(bananas)}"

                # Вызываем твой логгер
                # stats=None, так как из вебхука мы не знаем историю покупок, 
                # логгер пометит его как "Новичок" или просто покажет сумму.
                await log_payment(
                    bot=bot,
                    user=user_info,
                    amount=int(amount),
                    item_name=item_name,
                    new_balance=0, # Тут можно поставить 0, т.к. это поле не критично
                    stats=None 
                )
            except Exception as e:
                print(f"⚠️ Ошибка отправки лога админу: {e}")

    return web.Response(status=200)

async def start_webhook_server(bot):
    app = web.Application()
    app['bot'] = bot 
    app.router.add_post('/yookassa_webhook', handle_yookassa_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 5001)
    await site.start()
    print("🚀 Webhook Server (Dynamic Prices) запущен на порту 5001")