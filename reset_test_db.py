import asyncio
from app.database import async_session
from app.models import User, AdScenario, Purchase
from sqlalchemy import delete

async def reset():
    async with async_session() as session:
        # Удаляем историю покупок, чтобы has_user_purchased возвращал False в тестах
        await session.execute(delete(Purchase))
        print("✅ Все покупки удалены")

        # Удаляем всех пользователей
        await session.execute(delete(User))
        print("✅ Все пользователи удалены")
        
        # Удаляем все сценарии
        await session.execute(delete(AdScenario))
        print("✅ Все сценарии удалены")
        
        await session.commit()
        print("✅ База очищена!")

if __name__ == "__main__":
    asyncio.run(reset())