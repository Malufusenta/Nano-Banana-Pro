import sqlite3
import os

# Имя файла базы данных (проверь, как он называется на сервере, обычно banana.db)
DB_NAME = "bot.db"

def fix_history():
    print(f"🛠 Подключаюсь к базе {DB_NAME}...")
    
    if not os.path.exists(DB_NAME):
        print(f"❌ Ошибка: Файл {DB_NAME} не найден!")
        return

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # 1. Проверяем, есть ли там вообще записи
        cursor.execute("SELECT count(*) FROM purchases")
        total = cursor.fetchone()[0]
        print(f"📊 Всего записей в таблице покупок: {total}")

        # 2. Обновляем статус всех записей на 'succeeded'
        # (Так как раньше у нас не было отмен, считаем всё, что было в базе — успешным)
        print("⏳ Обновляю статусы...")
        cursor.execute("UPDATE purchases SET status = 'succeeded' WHERE status != 'succeeded'")
        
        # Узнаем, сколько записей обновили
        updated_rows = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        print(f"✅ Успешно! Обновлено {updated_rows} записей.")
        print("Теперь статистика в админке должна отображаться корректно.")
        
    except Exception as e:
        print(f"❌ Ошибка при работе с базой: {e}")

if __name__ == "__main__":
    fix_history()