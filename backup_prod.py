import subprocess
from datetime import datetime
import os
import time
from urllib.parse import urlparse
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Получаем строку подключения
db_url = os.getenv("DATABASE_URL")

if not db_url:
    print("❌ Ошибка: DATABASE_URL не найден в .env")
    exit(1)

# Парсим строку (postgresql+asyncpg://user:pass@host:port/dbname)
# Заменяем протокол на стандартный для корректной работы urlparse
parsed = urlparse(db_url.replace("postgresql+asyncpg://", "http://"))

db_user = parsed.username
db_pass = parsed.password
db_host = parsed.hostname or "localhost"
db_port = parsed.port or "5432"
db_name = parsed.path.lstrip('/')

# Настройки для бэкапа
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_dir = "my_backups"
backup_file = f"{backup_dir}/backup_prod_{timestamp}.sql"

# Создаем папку для бэкапов, если её нет
if not os.path.exists(backup_dir):
    os.makedirs(backup_dir)

print(f"🔄 Создаю бэкап PostgreSQL базы '{db_name}'...")

# Формируем команду pg_dump
cmd = f"PGPASSWORD='{db_pass}' pg_dump -h {db_host} -p {db_port} -U {db_user} -d {db_name} -F c -f {backup_file}"

try:
    # Запускаем создание бэкапа
    subprocess.run(cmd, shell=True, check=True)
    
    # Проверяем размер файла
    file_size = os.path.getsize(backup_file) / 1024 / 1024
    print(f"✅ Бэкап создан: {backup_file}")
    print(f"📍 Размер: {file_size:.2f} MB")
    
except Exception as e:
    print(f"❌ Ошибка при выполнении pg_dump: {e}")
    exit(1)

# --- БЛОК ОЧИСТКИ СТАРЫХ ФАЙЛОВ (оставляем последние 7 дней) ---
print("🧹 Проверка старых бэкапов...")
now = time.time()
days_to_keep = 7

if os.path.exists(backup_dir):
    for f in os.listdir(backup_dir):
        f_path = os.path.join(backup_dir, f)
        
        # Проверяем, что это файл .sql
        if os.path.isfile(f_path) and f.endswith(".sql"):
            # Проверяем время изменения файла
            if os.stat(f_path).st_mtime < now - (days_to_keep * 86400):
                try:
                    os.remove(f_path)
                    print(f"🗑️ Удален старый бэкап: {f}")
                except Exception as e:
                    print(f"⚠️ Не удалось удалить {f}: {e}")
