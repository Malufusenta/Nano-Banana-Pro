import asyncio
from app.database import engine
from sqlalchemy import text

async def apply_migration():
    async with engine.begin() as conn:
        # Команда 1: Добавляем колонку
        await conn.execute(text("""
            ALTER TABLE users 
            ADD COLUMN IF NOT EXISTS first_generation_done BOOLEAN DEFAULT FALSE;
        """))
        print("✅ Колонка добавлена")
        
        # Команда 2: Обновляем существующие записи
        await conn.execute(text("""
            UPDATE users 
            SET first_generation_done = FALSE 
            WHERE first_generation_done IS NULL;
        """))
        print("✅ Данные обновлены")
    
    print("🎉 Миграция успешно применена!")

if __name__ == "__main__":
    asyncio.run(apply_migration())