# compare_db.py (для PostgreSQL)
import asyncio
from sqlalchemy import text
from app.database import async_session
from app.models import User

async def compare():
    print("="*60)
    print("🔍 СРАВНЕНИЕ: Модель vs Реальная БД (PostgreSQL)")
    print("="*60)
    
    async with async_session() as session:
        # Получаем реальную структуру из БД (PostgreSQL синтаксис)
        result = await session.execute(
            text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'users' 
                ORDER BY ordinal_position
            """)
        )
        db_columns = {row[0] for row in result.fetchall()}
    
    # Получаем колонки из модели
    model_columns = {col.name for col in User.__table__.columns}
    
    print("\n📊 Колонки в МОДЕЛИ (models.py):")
    for col in sorted(model_columns):
        print(f"  ✓ {col}")
    
    print(f"\n  Всего в модели: {len(model_columns)}")
    
    print("\n📊 Колонки в РЕАЛЬНОЙ БД:")
    for col in sorted(db_columns):
        print(f"  ✓ {col}")
    
    print(f"\n  Всего в БД: {len(db_columns)}")
    
    # Находим различия
    missing_in_db = model_columns - db_columns
    extra_in_db = db_columns - model_columns
    
    print("\n" + "="*60)
    
    if missing_in_db:
        print("⚠️  КОЛОНКИ ЕСТЬ В МОДЕЛИ, НО ОТСУТСТВУЮТ В БД:")
        for col in sorted(missing_in_db):
            print(f"  ❌ {col}")
        print("\n→ Эти колонки нужно добавить через миграцию")
    
    if extra_in_db:
        print("\n⚠️  КОЛОНКИ ЕСТЬ В БД, НО ОТСУТСТВУЮТ В МОДЕЛИ:")
        for col in sorted(extra_in_db):
            print(f"  ❓ {col}")
        print("\n→ Возможно были ручные правки БД или старая модель")
    
    if not missing_in_db and not extra_in_db:
        print("✅ БД ПОЛНОСТЬЮ СИНХРОНИЗИРОВАНА С МОДЕЛЬЮ!")
        print("→ Можно безопасно добавлять новые колонки")
    
    print("="*60)

asyncio.run(compare())
