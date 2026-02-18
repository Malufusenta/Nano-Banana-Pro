import asyncio
import sys
from datetime import datetime, timedelta
from sqlalchemy import select

# Импорты
try:
    from app.models import Purchase
    from app.database import async_session
except ImportError:
    from models import Purchase
    from database import async_session

async def check_all_transactions():
    print("📊 ВЫВОДИМ ВСЕ ТРАНЗАКЦИИ ЗА 48 ЧАСОВ (Успешные и нет)")
    
    # Берем за 2 дня
    time_threshold = datetime.utcnow() - timedelta(days=2)
    
    async with async_session() as session:
        # Убрали фильтр по статусу — показываем ВСЁ
        query = select(Purchase).where(
            Purchase.created_at >= time_threshold,
            Purchase.price > 0
        ).order_by(Purchase.created_at.desc())

        result = await session.execute(query)
        purchases = result.scalars().all()

        print("-" * 105)
        print(f"{'ID':<6} | {'User ID':<12} | {'Сумма':<8} | {'Статус':<12} | {'Дата (UTC)'}")
        print("-" * 105)

        total_db_sum = 0
        
        for p in purchases:
            # Визуально выделим НЕ успешные
            marker = "❌" if p.status != 'succeeded' else "✅"
            print(f"{marker} {p.id:<4} | {p.user_id:<12} | {p.price:<8} | {p.status:<12} | {p.created_at}")
            
            if p.status == 'succeeded':
                total_db_sum += p.price

        print("-" * 105)
        print(f"💰 Сумма УСПЕШНЫХ в базе: {total_db_sum} RUB")
        print("Сравни этот список с ЮKassa.")
        print("Если платежа тут НЕТ — значит он 'призрак'. Начисли бананы вручную.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(check_all_transactions())
