import asyncio
from sqlalchemy import select, func
from app.database import async_session
from app.models import User, Purchase
from datetime import datetime

async def migrate_old_purchases():
    """
    Заполняет недостающие поля в старых покупках и обновляет LTV метрики юзеров
    """
    
    async with async_session() as session:
        print("🔍 Анализирую базу данных...")
        
        # 1. Считаем сколько покупок без аналитики
        purchases_without_analytics = await session.scalar(
            select(func.count(Purchase.id)).where(
                Purchase.status == 'succeeded',
                Purchase.tariff_name == None
            )
        )
        
        print(f"\n📊 Найдено покупок без аналитики: {purchases_without_analytics}")
        
        if purchases_without_analytics == 0:
            print("✅ Все покупки уже обработаны!")
            return
        
        # Просим подтверждение
        confirm = input(f"\n⚠️  Будет обновлено {purchases_without_analytics} записей. Продолжить? (yes/no): ")
        
        if confirm.lower() != 'yes':
            print("❌ Отменено")
            return
        
        print("\n🚀 Начинаю миграцию...\n")
        
        # 2. Обновляем Purchase: добавляем tariff_name, user_source, completed_at
    import asyncio
from sqlalchemy import select, func
from app.database import async_session
from app.models import User, Purchase
from datetime import datetime

async def migrate_old_purchases():
    """
    Заполняет недостающие поля в старых покупках и обновляет LTV метрики юзеров
    """
    
    async with async_session() as session:
        print("🔍 Анализирую базу данных...")
        
        # 1. Считаем сколько покупок без аналитики
        purchases_without_analytics = await session.scalar(
            select(func.count(Purchase.id)).where(
                Purchase.status == 'succeeded',
                Purchase.tariff_name == None
            )
        )
        
        print(f"\n📊 Найдено покупок без аналитики: {purchases_without_analytics}")
        
        if purchases_without_analytics == 0:
            print("✅ Все покупки уже обработаны!")
            return
        
        # Просим подтверждение
        confirm = input(f"\n⚠️  Будет обновлено {purchases_without_analytics} записей. Продолжить? (yes/no): ")
        
        if confirm.lower() != 'yes':
            print("❌ Отменено")
            return
        
        print("\n🚀 Начинаю миграцию...\n")
        
        # 2. Обновляем Purchase: добавляем tariff_name, user_source, completed_at
        purchases = await session.execute(
            select(Purchase).where(
                Purchase.status == 'succeeded',
                Purchase.tariff_name == None
            )
        )
        
        updated_purchases = 0
        
        for purchase in purchases.scalars():
            # Определяем тариф по цене
            tariff_map = {
                79.0: "8 бананов",
                299.0: "44 банана",
                699.0: "140 бананов",
                1499.0: "340 бананов",
                3499.0: "832 банана"
            }
            
            purchase.tariff_name = tariff_map.get(purchase.price, f"Unknown ({purchase.price}₽)")
            
            # Получаем source юзера
            user_result = await session.execute(
                select(User).where(User.telegram_id == purchase.user_id)
            )
            user = user_result.scalar_one_or_none()
            
            if user:
                purchase.user_source = user.source or "organic"
            
            # Если нет completed_at - ставим created_at
            if not purchase.completed_at:
                purchase.completed_at = purchase.created_at
            
            updated_purchases += 1
            
            if updated_purchases % 10 == 0:
                print(f"  📝 Обработано покупок: {updated_purchases}/{purchases_without_analytics}")
        
        print(f"✅ Обновлено {updated_purchases} покупок\n")
        
        # 3. Обновляем User: total_revenue, orders_count, first_purchase_at
        print("👤 Пересчитываю метрики пользователей...")
        
        users_with_purchases = await session.execute(
            select(User).where(
                User.telegram_id.in_(
                    select(Purchase.user_id).where(Purchase.status == 'succeeded')
                )
            )
        )
        
        updated_users = 0
        
        for user in users_with_purchases.scalars():
            # Считаем выручку и количество покупок
            user_purchases = await session.execute(
                select(Purchase).where(
                    Purchase.user_id == user.telegram_id,
                    Purchase.status == 'succeeded'
                ).order_by(Purchase.completed_at)
            )
            
            purchases_list = list(user_purchases.scalars())
            
            if not purchases_list:
                continue
            
            # Обновляем метрики
            user.orders_count = len(purchases_list)
            user.total_revenue = sum(p.price for p in purchases_list)
            user.first_purchase_at = purchases_list[0].completed_at
            
            # Проверяем флаг "копил или сразу купил"
            # Для старых данных мы не знаем точно, ставим False (считаем что сразу купил)
            user.had_free_actions_before_purchase = False
            
            updated_users += 1
            
            if updated_users % 10 == 0:
                print(f"  👤 Обработано пользователей: {updated_users}")
        
        print(f"✅ Обновлено {updated_users} пользователей\n")
        
        # 4. Коммитим всё
        await session.commit()
        
        print("=" * 50)
        print("🎉 МИГРАЦИЯ ЗАВЕРШЕНА!")
        print("=" * 50)
        print(f"✅ Обновлено покупок: {updated_purchases}")
        print(f"✅ Обновлено пользователей: {updated_users}")
        print("\n💡 Теперь статистика будет работать с историческими данными!")

if __name__ == "__main__":
    asyncio.run(migrate_old_purchases())
