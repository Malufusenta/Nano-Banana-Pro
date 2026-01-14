import asyncio
from app.database import engine
from sqlalchemy import text

async def fix_all_sequences():
    async with engine.begin() as conn:
        # Исправляем users
        result = await conn.execute(text("SELECT MAX(id) FROM users;"))
        max_id = result.scalar()
        if max_id:
            await conn.execute(text(f"SELECT setval('users_id_seq', {max_id});"))
            print(f"✅ users_id_seq установлена на {max_id}")
        
        # Исправляем message_history
        result = await conn.execute(text("SELECT MAX(id) FROM message_history;"))
        max_id = result.scalar()
        if max_id:
            await conn.execute(text(f"SELECT setval('message_history_id_seq', {max_id});"))
            print(f"✅ message_history_id_seq установлена на {max_id}")
        
        # Исправляем все остальные таблицы
        tables = ['purchases', 'generation_tasks', 'broadcasts', 'post_configs', 'banana_transactions']
        for table in tables:
            result = await conn.execute(text(f"SELECT MAX(id) FROM {table};"))
            max_id = result.scalar()
            if max_id:
                await conn.execute(text(f"SELECT setval('{table}_id_seq', {max_id});"))
                print(f"✅ {table}_id_seq установлена на {max_id}")
    
    print("🎉 Все sequences исправлены!")

if __name__ == "__main__":
    asyncio.run(fix_all_sequences())