import asyncio
import sqlite3
from sqlalchemy import select
from app.database import async_session, engine
from app.models import Base, User, Purchase, MessageHistory, GenerationTask, Broadcast, PostConfig, BananaTransaction
from datetime import datetime

async def migrate_data():
    print("🚀 Начинаю миграцию...\n")
    
    # Создаем таблицы в PostgreSQL
# Удаляем старые таблицы и создаем новые
    print("🗑️  Очищаю старые таблицы...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Таблицы созданы\n")
    
    # Читаем из SQLite
    print("📂 Читаю bot.db...")
    conn = sqlite3.connect('bot.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    async with async_session() as session:
        # USERS
        print("👥 Мигрирую пользователей...")
        cursor.execute("SELECT * FROM users")
        users = cursor.fetchall()
        
        for row in users:
            user = User(
                id=row['id'],
                telegram_id=row['telegram_id'],
                username=row['username'],
                full_name=row['full_name'],
                generations_balance=row['generations_balance'],
                balance_free=row['balance_free'],
                balance_paid=row['balance_paid'],
                total_generations_used=row['total_generations_used'],
                last_generation_at=datetime.fromisoformat(row['last_generation_at']) if row['last_generation_at'] else None,
                is_sub_bonus_claimed=bool(row['is_sub_bonus_claimed']),
                preferred_model=row['preferred_model'],
                is_blocked=bool(row['is_blocked']),
                is_channel_sub_claimed=bool(row['is_channel_sub_claimed']),
                is_chat_sub_claimed=bool(row['is_chat_sub_claimed']),
                referrer_id=row['referrer_id'],
                source=row['source'],
                created_at=datetime.fromisoformat(row['created_at']),
                total_revenue=0,
                orders_count=0,
                first_purchase_at=None,
                had_free_actions_before_purchase=False
            )
            session.add(user)
        print(f"✅ Пользователей: {len(users)}")
        
        # PURCHASES
        print("💰 Мигрирую покупки...")
        cursor.execute("SELECT * FROM purchases")
        purchases = cursor.fetchall()
        
        for row in purchases:
            purchase = Purchase(
                id=row['id'],
                user_id=row['user_id'],
                amount=row['amount'],
                price=row['price'],
                status=row['status'],
                created_at=datetime.fromisoformat(row['created_at']),
                tariff_name=None,
                user_source=None,
                completed_at=None,
                payment_id=None
            )
            session.add(purchase)
        print(f"✅ Покупок: {len(purchases)}")
        
        # MESSAGE_HISTORY
        print("💬 Мигрирую историю сообщений...")
        cursor.execute("SELECT * FROM message_history")
        messages = cursor.fetchall()
        
        for row in messages:
            msg = MessageHistory(
                id=row['id'],
                user_id=row['user_id'],
                role=row['role'],
                content=row['content'],
                has_image=bool(row['has_image']),
                file_id=row['file_id'],
                image_url=row['image_url'],
                created_at=datetime.fromisoformat(row['created_at'])
            )
            session.add(msg)
        print(f"✅ Сообщений: {len(messages)}")
        
# GENERATION_TASKS
        print("🎨 Мигрирую задачи генерации...")
        cursor.execute("SELECT * FROM generation_tasks")
        tasks = cursor.fetchall()
        
        for row in tasks:
            try:
                deducted_paid = row['deducted_from_paid']
                deducted_free = row['deducted_from_free']
            except (KeyError, IndexError):
                deducted_paid = 0
                deducted_free = 0
                
            task = GenerationTask(
                id=row['id'],
                user_id=row['user_id'],
                cost=row['cost'],
                status=row['status'],
                deducted_from_paid=row['deducted_from_paid'],
                deducted_from_free=row['deducted_from_free'],
                created_at=datetime.fromisoformat(row['created_at'])
            )
            session.add(task)
        print(f"✅ Задач: {len(tasks)}")
        
        # BROADCASTS
        print("📢 Мигрирую рассылки...")
        cursor.execute("SELECT * FROM broadcasts")
        broadcasts = cursor.fetchall()
        
        for row in broadcasts:
            bc = Broadcast(
                id=row['id'],
                admin_id=row['admin_id'],
                message_text=row['message_text'],
                media_type=row['media_type'],
                media_file_ids=row['media_file_ids'],
                buttons=row['buttons'],
                hidden_prompt=row['hidden_prompt'],
                aspect_ratio=row['aspect_ratio'],
                status=row['status'],
                total_users=row['total_users'],
                sent_count=row['sent_count'],
                delivered_count=row['delivered_count'],
                blocked_count=row['blocked_count'],
                created_at=datetime.fromisoformat(row['created_at']),
                started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
                completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None
            )
            session.add(bc)
        print(f"✅ Рассылок: {len(broadcasts)}")
        
        # POST_CONFIGS
        print("🔗 Мигрирую конфиги постов...")
        cursor.execute("SELECT * FROM post_configs")
        configs = cursor.fetchall()
        
        for row in configs:
            cfg = PostConfig(
                id=row['id'],
                config_id=row['config_id'],
                prompt=row['prompt'],
                model_type=row['model_type'],
                aspect_ratio=row['aspect_ratio'],
                created_by=row['created_by'],
                created_at=datetime.fromisoformat(row['created_at']),
                clicks_count=row['clicks_count']
            )
            session.add(cfg)
        print(f"✅ Конфигов: {len(configs)}")

        print("\n💾 Сохраняю...")
        await session.commit()

# BANANA_TRANSACTIONS
        print("🍌 Мигрирую транзакции бананов...")
        try:
            cursor.execute("SELECT * FROM banana_transactions")
            transactions = cursor.fetchall()
            
            for row in transactions:
                tx = BananaTransaction(
                    id=row['id'],
                    user_id=row['user_id'],
                    amount=row['amount'],
                    transaction_type=row['transaction_type'],
                    description=row['description'],
                    created_at=datetime.fromisoformat(row['created_at'])
                )
                session.add(tx)
            print(f"✅ Транзакций: {len(transactions)}")
        except Exception as e:
            print(f"⚠️  Таблица banana_transactions не найдена (пропускаем)")
    conn.close()

    
    
    # Проверка
    async with async_session() as session:
        result = await session.execute(select(User))
        count = len(result.scalars().all())
        print(f"\n✅ В PostgreSQL: {count} пользователей")
    
    print("🎉 ГОТОВО!")

if __name__ == "__main__":
    asyncio.run(migrate_data())