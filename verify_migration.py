import asyncio
import os
from sqlalchemy import select, func
from app.database import async_session
from app.models import User, Purchase, BananaTransaction, GenerationTask, MessageHistory, Broadcast, PostConfig

# КОНТРОЛЬНЫЕ СУММЫ ИЗ SQLite
EXPECTED = {
    'users': 1849,
    'banana_transactions': 5472,
    'purchases': 480,
    'generation_tasks': 22,
    'message_history': 12612,
    'balance_free_sum': 1528,
    'balance_paid_sum': 1048,
    'users_with_balance': 816,
    'total_revenue': 49268
}

TEST_USERS = {
    627352144: {'free': 93, 'paid': 12},
    5110520283: {'free': 0, 'paid': 18},
    7300811205: {'free': 0, 'paid': 25}
}

async def verify():
    print("🔍 ПРОВЕРКА МИГРАЦИИ\n")
    print("=" * 50)
    
    errors = []
    
    async with async_session() as session:
        # 1. Количество записей
        print("\n📊 Количество записей:")
        
        users_count = (await session.execute(select(func.count(User.id)))).scalar()
        print(f"  👥 Пользователей: {users_count} (ожидалось {EXPECTED['users']})")
        if users_count != EXPECTED['users']:
            errors.append(f"❌ Пользователей: {users_count} != {EXPECTED['users']}")
        
        banana_count = (await session.execute(select(func.count(BananaTransaction.id)))).scalar()
        print(f"  🍌 Транзакций бананов: {banana_count} (ожидалось {EXPECTED['banana_transactions']})")
        if banana_count != EXPECTED['banana_transactions']:
            errors.append(f"❌ Транзакций бананов: {banana_count} != {EXPECTED['banana_transactions']}")
        
        purchases_count = (await session.execute(select(func.count(Purchase.id)))).scalar()
        print(f"  💰 Покупок: {purchases_count} (ожидалось {EXPECTED['purchases']})")
        if purchases_count != EXPECTED['purchases']:
            errors.append(f"❌ Покупок: {purchases_count} != {EXPECTED['purchases']}")
        
        tasks_count = (await session.execute(select(func.count(GenerationTask.id)))).scalar()
        print(f"  🎨 Задач: {tasks_count} (ожидалось {EXPECTED['generation_tasks']})")
        if tasks_count != EXPECTED['generation_tasks']:
            errors.append(f"❌ Задач: {tasks_count} != {EXPECTED['generation_tasks']}")
        
        messages_count = (await session.execute(select(func.count(MessageHistory.id)))).scalar()
        print(f"  💬 Сообщений: {messages_count} (ожидалось {EXPECTED['message_history']})")
        if messages_count != EXPECTED['message_history']:
            errors.append(f"❌ Сообщений: {messages_count} != {EXPECTED['message_history']}")
        
        # 2. Суммы балансов (КРИТИЧНО!)
        print("\n💵 КРИТИЧЕСКИЕ ПРОВЕРКИ БАЛАНСОВ:")
        
        total_free = (await session.execute(select(func.sum(User.balance_free)))).scalar() or 0
        print(f"  🎁 Бесплатных бананов: {total_free} (ожидалось {EXPECTED['balance_free_sum']})")
        if total_free != EXPECTED['balance_free_sum']:
            errors.append(f"❌ КРИТИЧНО! Бесплатных бананов: {total_free} != {EXPECTED['balance_free_sum']}")
        
        total_paid = (await session.execute(select(func.sum(User.balance_paid)))).scalar() or 0
        print(f"  💳 Платных бананов: {total_paid} (ожидалось {EXPECTED['balance_paid_sum']})")
        if total_paid != EXPECTED['balance_paid_sum']:
            errors.append(f"❌ КРИТИЧНО! Платных бананов: {total_paid} != {EXPECTED['balance_paid_sum']}")
        
        users_with_balance = (await session.execute(
            select(func.count(User.id)).where((User.balance_free > 0) | (User.balance_paid > 0))
        )).scalar()
        print(f"  👤 Пользователей с балансом: {users_with_balance} (ожидалось {EXPECTED['users_with_balance']})")
        if users_with_balance != EXPECTED['users_with_balance']:
            errors.append(f"❌ Пользователей с балансом: {users_with_balance} != {EXPECTED['users_with_balance']}")
        
        total_revenue = (await session.execute(select(func.sum(User.total_revenue)))).scalar() or 0
        print(f"  📊 Общая выручка: {total_revenue} (ожидалось {EXPECTED['total_revenue']})")
        if total_revenue != EXPECTED['total_revenue']:
            errors.append(f"❌ Общая выручка: {total_revenue} != {EXPECTED['total_revenue']}")
        
        # 3. Проверка конкретных пользователей
        print("\n👤 Проверка тестовых пользователей:")
        for telegram_id, expected in TEST_USERS.items():
            user = (await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )).scalar_one_or_none()
            
            if not user:
                errors.append(f"❌ Пользователь {telegram_id} не найден!")
                print(f"  ❌ {telegram_id}: НЕ НАЙДЕН!")
            else:
                status = "✅" if (user.balance_free == expected['free'] and user.balance_paid == expected['paid']) else "❌"
                print(f"  {status} {telegram_id}: free={user.balance_free} (ожидалось {expected['free']}), paid={user.balance_paid} (ожидалось {expected['paid']})")
                if user.balance_free != expected['free']:
                    errors.append(f"❌ {telegram_id}: balance_free {user.balance_free} != {expected['free']}")
                if user.balance_paid != expected['paid']:
                    errors.append(f"❌ {telegram_id}: balance_paid {user.balance_paid} != {expected['paid']}")
    
    # Итог
    print("\n" + "=" * 50)
    if errors:
        print("❌ МИГРАЦИЯ ПРОВАЛИЛАСЬ!\n")
        print("Ошибки:")
        for error in errors:
            print(f"  {error}")
        print("\n⚠️  НЕ ЗАПУСКАЙ БОТА! ОТКАТ К SQLite!")
        return False
    else:
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ!")
        print("🎉 Миграция успешна! Можно запускать бота.")
        return True

if __name__ == "__main__":
    success = asyncio.run(verify())
    exit(0 if success else 1)
