import asyncio
from app.database import engine
from sqlalchemy import text

async def rollback():
    confirm = input("⚠️ ОТКАТ МИГРАЦИИ! Удалить колонку first_generation_done? (напиши 'да'): ")
    if confirm.lower() != 'да':
        print("❌ Отменено")
        return
    
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS first_generation_done;"))
        print("✅ Откат завершен")

asyncio.run(rollback())
