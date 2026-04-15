from sqlalchemy import BigInteger, String, Integer, DateTime, func, Boolean, Text, Column, Numeric, Index
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
    yandex_client_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    active_scenario_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


    
    # Баланс
    generations_balance: Mapped[int] = mapped_column(Integer, default=0)
    balance_free: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    balance_paid: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_generations_used: Mapped[int] = mapped_column(Integer, default=0)
    last_generation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # ← ДОБАВЬ

    
    # Бонус и Настройки
    is_sub_bonus_claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    preferred_model: Mapped[str] = mapped_column(String, default="standard") # standard / pro / nb2
    is_model_manually_selected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    generations_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    first_generation_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


    is_channel_sub_claimed: Mapped[bool] = mapped_column(Boolean, default=False) # Канал
    is_chat_sub_claimed: Mapped[bool] = mapped_column(Boolean, default=False)    # Чат
    referrer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)   # Кто пригласил
    source: Mapped[str | None] = mapped_column(String, nullable=True) # Источник трафика

    # Аналитика покупок (LTV)
    total_revenue: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    orders_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    first_purchase_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    had_free_actions_before_purchase: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    visited_shop_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Воронка: зашёл в магазин

    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('ix_users_referrer_id', 'referrer_id'),
        Index('ix_users_source', 'source'),
    )

# 2. Таблица Покупок
class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="pending")

    # Аналитика
    tariff_name: Mapped[str | None] = mapped_column(String, nullable=True)
    user_source: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    # "yookassa_card", "yookassa_sbp", "telegram_stars"
    # нужно для блока 1: Stars отдельной строкой

    income_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # сумма после комиссии ЮKassa (income_amount из вебхука)
    # для блока 1: чистая выручка копейка в копейку

    is_first_purchase: Mapped[bool] = mapped_column(Boolean, default=False)
    # нужно для блока 4: новые vs старые покупатели
    # и для блока 3: CAC = директ / кол-во новых

    __table_args__ = (
        Index('ix_purchases_user_status', 'user_id', 'status'),
        Index('ix_purchases_payment_id', 'payment_id', unique=True),
    )

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
    deducted_from_paid: Mapped[int] = mapped_column(Integer, server_default='0', nullable=False)
    deducted_from_free: Mapped[int] = mapped_column(Integer, server_default='0', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    post_id: Mapped[str] = mapped_column(String, nullable=True)
    # ДОБАВИТЬ:
    kie_credits_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Стоимость генерации в кредитах kie.ai (1 кредит = $0.005)
    model_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # "standard", "pro", "nb2" — для аналитики по моделям

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
    # Опционально: вопрос перед генерацией; подстановка в hidden_prompt с {value}
    param_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    aspect_ratio = Column(String(10), default="1:1")
    model_type = Column(String(20), default="standard")  # 👈 ДОБАВИТЬ
    
    # Статистика
    status: Mapped[str] = mapped_column(String, default="draft")  # draft, sending, completed
    total_users: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    clicks_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 6. Таблица конфигов для постов (Deep Linking)
class PostConfig(Base):
    __tablename__ = "post_configs"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    config_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)  # "post_55"
    
    # Настройки генерации
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Опционально: вопрос перед генерацией; подстановка в prompt с {value}
    param_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_type: Mapped[str] = mapped_column(String(20), default="standard")  # standard / pro
    aspect_ratio: Mapped[str] = mapped_column(String(10), default="1:1")
    
    # Метаданные
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)  # ID админа
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    
    # Статистика использования (опционально)
    clicks_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

# После класса PostConfig добавь:

class AdScenario(Base):
    """Рекламные сценарии для Deep Linking с Яндекс.Метрикой"""
    __tablename__ = "ad_scenarios"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scenario_key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    welcome_text: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_type: Mapped[str] = mapped_column(String(20), default="standard")
    aspect_ratio: Mapped[str] = mapped_column(String(10), default="1:1")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    
    # Статистика
    total_starts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_purchases: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 7. Таблица транзакций бананов (детальный трекинг)
class BananaTransaction(Base):
    __tablename__ = "banana_transactions"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(Integer)  # +10 или -1
    transaction_type: Mapped[str] = mapped_column(String)  # "spent", "earned_ref", "earned_sub", "purchased", "welcome"
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    post_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kie_credits_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_type: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index('ix_banana_tx_user_id', 'user_id'),
        Index('ix_banana_tx_user_type', 'user_id', 'transaction_type'),
    )

# 8. Таблица задач генерации видео
class VideoGenerationTask(Base):
    __tablename__ = "video_generation_tasks"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    
    # Kling API
    task_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)  # ID задачи от Kling
    
    # Исходник
    source_image_file_id: Mapped[str] = mapped_column(String, nullable=False)  # file_id картинки из Telegram
    source_image_url: Mapped[str | None] = mapped_column(String, nullable=True)  # URL картинки (если загружали)
    
    # Результат
    status: Mapped[str] = mapped_column(String, default="waiting")  # waiting, success, fail
    result_video_url: Mapped[str | None] = mapped_column(String, nullable=True)  # URL готового видео
    result_video_file_id: Mapped[str | None] = mapped_column(String, nullable=True)  # file_id видео в Telegram (после отправки)
    
    # Ошибки
    fail_code: Mapped[str | None] = mapped_column(String, nullable=True)
    fail_message: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Финансы
    cost: Mapped[int] = mapped_column(Integer, default=10)  # 10 бананов
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)  # Возврат средств при ошибке
    kie_credits_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class FixedExpense(Base):
    """Фиксированные расходы (сервер, Tilda, Claude и т.д.)"""
    __tablename__ = "fixed_expenses"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)  # "Сервер", "Tilda", "Claude"
    amount_rub: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # Сумма в рублях за месяц
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class CampaignMapping(Base):
    __tablename__ = "campaign_mappings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    yandex_campaign_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # Например: "Rsya_3 от 06-01-2026"
    utm_sources: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON-массив: ["yandex_rsya_3", "ad_yandex_rsya_3__cid", "ad_yandex_rsya_3"]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())