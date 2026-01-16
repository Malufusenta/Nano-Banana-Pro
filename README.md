## 📖 О проекте
**Nano-Banana-Pro** — это Telegram-бот, который использует AI для генерации изображений. Пользователи покупают виртуальную валюту (**"бананы"**) 🍌 и тратят её на создание уникальных картинок через Flux модели.

Проект включает полноценную систему монетизации, реферальную программу, модерацию контента и продвинутую аналитику.

## ✨ Ключевые особенности
* 🎨 **AI-генерация** изображений через Flux модели.
* 💰 **Двойная система оплаты:** Telegram Stars и YooKassa.
* 🍌 **Виртуальная валюта** с гибкими пакетами.
* 👥 **Реферальная программа** для привлечения пользователей.
* 🛡️ **Система модерации** с жалобами и блокировками.
* 📊 **Детальная аналитика** доходов и активности.
* 📢 **Broadcast-рассылки** для пользователей.
* 🔍 **Детектор blend-изображений**.

---

## 🚀 Возможности

### Для пользователей
* Генерация изображений по текстовому описанию.
* Покупка "бананов" через удобный интерфейс.
* Реферальные бонусы за приглашение друзей.
* История сгенерированных изображений.
* Жалобы на неуместный контент.

### Для администраторов
**📊 Аналитическая панель**
* Статистика выручки (дневная, месячная, годовая).
* Учет часовых поясов для точных данных.
* Графики активности пользователей.
* Топ пользователей по генерациям.

**👤 Управление пользователями**
* Просмотр профилей и статистики.
* Начисление/списание бананов.
* Блокировка и разблокировка.
* Поиск по ID.

**📢 Рассылки**
* Broadcast-сообщения всем пользователям.
* Статистика доставки.
* Отчеты об ошибках.

**🛡️ Модерация**
* Обработка жалоб.
* Просмотр контента.
* Блокировка нарушителей.

---

## 🛠️ Технологии

### Core Stack
* **Python 3.10+** — основной язык.
* **aiogram 3.x** — фреймворк для Telegram Bot API.
* **SQLAlchemy 2.x** — ORM для работы с БД.
* **asyncio** — асинхронная обработка.

### AI & External Services
* **Google Gemini API** — AI для обработки запросов.
* **Flux Models** — генерация изображений.
* **YooKassa** — платежная система.
* **Telegram Stars** — встроенные платежи Telegram.

### Infrastructure
* **SQLite/PostgreSQL** — база данных.
* **aiohttp** — HTTP клиент для API.
* **python-dotenv** — управление конфигурацией.
* **Pillow** — обработка изображений.

---

## 📦 Установка

### Требования
* Python 3.10+
* Telegram Bot Token
* API ключи для сервисов (Gemini, YooKassa)

### Шаги установки

1. **Клонируйте репозиторий**
   ```bash
   git clone https://github.com/Maxizag/Nano-Banana-Pro.git
   cd nano-banana-pro

```

2. **Создайте виртуальное окружение**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows

```


3. **Установите зависимости**
```bash
pip install -r requirements.txt

```


4. **Настройте конфигурацию**
Создайте файл `.env` на основе `.env.example`:
```bash
cp .env.example .env

```


*Заполните необходимые переменные (см. раздел Конфигурация).*
5. **Инициализируйте базу данных**
```bash
python -m alembic upgrade head

```


6. **Запустите бота**
```bash
python main.py

```



---

## ⚙️ Конфигурация

### Переменные окружения (.env)

```ini
# Telegram Bot
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=123456789,987654321

# Database
DATABASE_URL=sqlite+aiosqlite:///./bot.db

# Payment Systems
YOOKASSA_SHOP_ID=your_shop_id
YOOKASSA_SECRET_KEY=your_secret_key

# AI Services
GEMINI_API_KEY=your_gemini_api_key
FLUX_API_KEY=your_flux_api_key

# Webhooks (optional)
WEBHOOK_URL=[https://yourdomain.com/webhook](https://yourdomain.com/webhook)
WEBHOOK_SECRET=your_webhook_secret

# Other
TIMEZONE=Asia/Bangkok

```

### Цены и пакеты (config/packages.py)

```python
BANANA_PACKAGES = {
    'small': {'bananas': 10, 'price': 100, 'stars': 50},
    'medium': {'bananas': 25, 'price': 200, 'stars': 100},
    'large': {'bananas': 50, 'price': 350, 'stars': 175},
}

```

---

## 💻 Использование

### Команды бота

**Пользовательские:**

* `/start` — запуск бота и приветствие
* `/buy` — покупка бананов
* `/balance` — проверка баланса
* `/generate <промпт>` — генерация изображения
* `/referral` — реферальная ссылка и статистика

**Админские:**

* `/admin` — открыть админ-панель
* `/stats` — статистика и аналитика
* `/broadcast` — рассылка сообщений
* `/user <id>` — информация о пользователе

### Пример генерации

> `/generate a cute cat sitting on a cloud, watercolor style`

---

## 📁 Структура проекта

```text
nano-banana-pro/
├── main.py                 # Точка входа
├── bot/
│   ├── handlers/           # Обработчики
│   │   ├── user.py
│   │   ├── admin.py
│   │   ├── payment.py
│   │   └── moderation.py
│   ├── keyboards/          # Клавиатуры
│   ├── middlewares/        # Middleware
│   └── utils/              # Утилиты
├── db/
│   ├── models.py           # SQLAlchemy модели
│   ├── database.py         # Подключение к БД
│   └── migrations/         # Alembic миграции
├── services/
│   ├── ai_service.py       # Работа с AI API
│   ├── payment_service.py  # Платежные системы
│   ├── analytics.py        # Аналитика
│   └── image_service.py    # Обработка изображений
├── config/                 # Конфиги
├── requirements.txt        # Зависимости
├── .env.example            # Пример .env
└── README.md               # Этот файл

```

---

## 🔧 Разработка

**Запуск в dev-режиме:**

```bash
python main.py --dev

```

**Линтеры и тесты:**

```bash
black .      # Форматирование
mypy .       # Проверка типов
pytest tests/ # Тесты

```

---

## 🚀 Деплой (Systemd)

Пример службы `/etc/systemd/system/banana-bot.service`:

```ini
[Unit]
Description=Nano Banana Pro Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/path/to/nano-banana-pro
Environment="PATH=/path/to/nano-banana-pro/venv/bin"
ExecStart=/path/to/nano-banana-pro/venv/bin/python main.py
Restart=always

[Install]
WantedBy=multi-user.target

```

---

## 📝 TODO

* [ ] Добавить поддержку других AI моделей
* [ ] Интеграция с криптовалютными платежами
* [ ] Многоязычность интерфейса
* [ ] Web-панель администратора
* [ ] Экспорт аналитики в CSV/Excel

---

## 📄 Лицензия

Этот проект распространяется под лицензией MIT.

---

<div align="center">

**Made with vibe coding 🎵**





Если проект понравился — поставь ⭐️

</div>

```

Удачи! С таким описанием проект выглядит очень солидно. 🚀
