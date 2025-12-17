import sqlite3

# Сумма, которую нужно убрать из статистики
TARGET_PRICE = 1499

def fix_specific_payment():
    print(f"🛠 Ищу ошибочные успешные платежи на сумму {TARGET_PRICE}₽...")
    
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        # 1. Сначала проверим, есть ли такие записи
        cursor.execute(
            "SELECT id, user_id, created_at FROM purchases WHERE price = ? AND status = 'succeeded'", 
            (TARGET_PRICE,)
        )
        rows = cursor.fetchall()
        
        if not rows:
            print("❌ Записей с такой суммой и статусом 'succeeded' не найдено.")
            conn.close()
            return

        print(f"🔎 Найдено записей: {len(rows)}")
        for row in rows:
            print(f"   -> ID платежа: {row[0]} | User ID: {row[1]} | Дата: {row[2]}")

        # 2. Меняем статус на 'canceled'
        print("\n⏳ Исправляю статус на 'canceled' (отменено)...")
        
        cursor.execute(
            "UPDATE purchases SET status = 'canceled' WHERE price = ? AND status = 'succeeded'",
            (TARGET_PRICE,)
        )
        
        conn.commit()
        conn.close()
        
        print(f"✅ Готово! Сумма {TARGET_PRICE}₽ убрана из статистики (статус изменен на canceled).")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    fix_specific_payment()