from aiohttp import web
from app.database import async_session
from app.services.user_service import add_paid_balance
# 👇 Импортируем тарифы из единого файла
from app.packages import PACKAGES 

# 👇 Хелпер для окончаний (оставляем как было)
def get_banana_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11: return "банан"
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20): return "банана"
    return "бананов"

# 👇 УМНАЯ ФУНКЦИЯ: Ищет количество бананов по цене из PACKAGES
def get_bananas_by_price(price):
    target_price = float(price)
    
    # Бежим по всем пакетам в нашем списке
    for key, pkg in PACKAGES.items():
        # Если цена платежа совпадает с ценой пакета
        if float(pkg['price']) == target_price:
            return pkg['gens']
            
    # Если такой цены нет в списке
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

        # 1. Автоматически определяем кол-во бананов
        bananas = get_bananas_by_price(amount)
        
        if bananas > 0:
            async with async_session() as session:
                await add_paid_balance(session, user_id, bananas)
            
            # 2. Отправляем сообщение
            try:
                suffix = get_banana_word(bananas)
                text = (
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"🍌 Начислено: <b>+{bananas} {suffix}</b>\n"
                    f"Спасибо за покупку! Можно снова творить 🎨"
                )
                await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
            except Exception as e:
                print(f"⚠️ Не удалось отправить сообщение: {e}")
        else:
            print(f"⚠️ Пришла сумма {amount}, но такого тарифа нет в packages.py!")

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