import asyncio
import sys
from datetime import datetime, timedelta
from sqlalchemy import select

# Импорты (те же, что и работали)
try:
    from app.models import Purchase
    from app.database import async_session
except ImportError:
    from models import Purchase
    from database import async_session

async def check_success_payments():
    # Берем срез побольше (48 часов), чтобы увидеть границы "дня"
    days_back = 2
    time_threshold = datetime.utcnow() - timedelta(days=days_back)
    
    print(f"📊 Анализируем УСПЕШНЫЕ платежи за последние {days_back} дня")
    print(f"📅 Начиная с (UTC): {time_threshold.strftime('%Y-%m-%d %H:%M:%S')}")

    async with async_session() as session:
        # Ищем ВСЕ успешные
        query = select(Purchase).where(
            Purchase.created_at >= time_threshold,
            Purchase.status == 'succeeded'
        ).order_by(Purchase.created_at.desc())

        result = await session.execute(query)
        purchases = result.scalars().all()

        print("-" * 105)
        print(f"{'ID':<6} | {'Сумма':<8} | {'Дата (Server/UTC)':<20} | {'User ID':<12} | {'Payment ID'}")
        print("-" * 105)

        total_sum = 0
        today_sum_utc = 0
        
        # Определяем "сегодня" по UTC (9 января)
        today_date = datetime.utcnow().date() # 2026-01-09

        for p in purchases:
            p_date = p.created_at
            # Маркер для визуального удобства (если дата совпадает с сегодня по UTC)
            marker = "✅" if p_date.date() == today_date else "  "
            
            print(f"{marker} {p.id:<4} | {p.amount:<8} | {str(p_date):<20} | {p.user_id:<12} | {p.payment_id}")
            
            total_sum += p.amount
            if p_date.date() == today_date:
                today_sum_utc += p.amount

        print("-" * 105)
        print(f"💰 Всего в списке (за 48ч): {total_sum} RUB")
        print(f"📅 Из них датированы 'сегодня' (по UTC времени сервера): {today_sum_utc} RUB")
        print("-" * 105)
        print("Сравни дату и время платежей здесь с временем в ЮKassa.")
        print("Если в ЮKassa есть платеж на 914р, которого НЕТ в этом списке — значит он вообще не попал в БД.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(check_success_payments())
