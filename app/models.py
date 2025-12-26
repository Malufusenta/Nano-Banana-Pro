from sqlalchemy import BigInteger, String, Integer, DateTime, func, Boolean, Text, Column
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from datetime import datetime

# 1. Таблица Пользователей
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Баланс
    generations_balance: Mapped[int] = mapped_column(Integer, default=3)
    balance_free: Mapped[int] = mapped_column(Integer, default=3)  # ← ДОБАВЬ
    balance_paid: Mapped[int] = mapped_column(Integer, default=0)  # ← ДОБАВЬ
    total_generations_used: Mapped[int] = mapped_column(Integer, default=0)
    last_generation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # ← ДОБАВЬ

    
    # Бонус и Настройки
    is_sub_bonus_claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    preferred_model: Mapped[str] = mapped_column(String, default="standard") # standard / pro
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # ← ДОБАВЬ



    is_channel_sub_claimed: Mapped[bool] = mapped_column(Boolean, default=False) # Канал
    is_chat_sub_claimed: Mapped[bool] = mapped_column(Boolean, default=False)    # Чат
    referrer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)   # Кто пригласил
    source: Mapped[str | None] = mapped_column(String, nullable=True) # Источник трафика

    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# 2. Таблица Покупок
class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# 3. Таблица Истории (Контекст + Галерея)
class MessageHistory(Base):
    __tablename__ = "message_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String) # 'user' или 'model'
    content: Mapped[str] = mapped_column(Text) # Текст или JSON настроек
    has_image: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # ID файла в Телеграм (для отправки)
    file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Ссылка на оригинал (для скачивания документа)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# 4. Таблица Активных Задач (Страховка от сбоев)
class GenerationTask(Base):
    __tablename__ = "generation_tasks"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="processing")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 5. Таблица Рассылок
class Broadcast(Base):
    __tablename__ = "broadcasts"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # Кто создал
    
    # Контент рассылки
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # Текст сообщения
    media_type: Mapped[str | None] = mapped_column(String, nullable=True)  # photo, video, album, None
    media_file_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON массив file_id
    
    # Кнопки (JSON)
    buttons: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: [{"text": "...", "type": "url/callback", "data": "..."}]
    
    # Для Type B кнопок - скрытый промпт
    hidden_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    aspect_ratio = Column(String(10), default="1:1")
    
    # Статистика
    status: Mapped[str] = mapped_column(String, default="draft")  # draft, sending, completed
    total_users: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)