import asyncio
from app.database import engine
from sqlalchemy import text

async def reset_database():
    """Полностью очищает тестовую БД"""
    
    confirm = input("⚠️ ТЫ УВЕРЕН? Это удалит ВСЕ данные! (напиши 'да'): ")
    if confirm.lower() != 'да':
        print("❌ Отменено")
        return
    
    async with engine.begin() as conn:
        print("🗑️ Удаляю все данные...")
        
        # Очищаем все таблицы
        await conn.execute(text("TRUNCATE TABLE users, message_history, purchases, generation_tasks, broadcasts, post_configs, banana_transactions RESTART IDENTITY CASCADE;"))
        
        print("✅ Все таблицы очищены!")
        print("✅ Sequences сброшены на 1!")
    
    print("🎉 База данных обнулена!")

if __name__ == "__main__":
    asyncio.run(reset_database())