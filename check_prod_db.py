# check_prod_db.py
import asyncio
from sqlalchemy import text
from app.database import async_session

async def check():
    async with async_session() as session:
        # Проверяем какие колонки есть в таблице users
        result = await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' ORDER BY ordinal_position")
        )
        columns = [row[0] for row in result.fetchall()]
        
        print("📊 Колонки в таблице users:")
        for col in columns:
            print(f"  - {col}")
        
        # Проверяем есть ли yandex_client_id
        if 'yandex_client_id' in columns:
            print("\n✅ Колонка yandex_client_id УЖЕ ЕСТЬ!")
        else:
            print("\n❌ Колонка yandex_client_id ОТСУТСТВУЕТ - нужна миграция")

asyncio.run(check())
