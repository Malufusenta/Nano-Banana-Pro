#!/usr/bin/env python3
"""
Nano Banana — ежедневный репортёр в Google Sheets
Запускается каждую ночь в 04:01 по Вьетнаму (UTC+7)
Пишет строку со статистикой за вчерашний день
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# ===================== НАСТРОЙКИ =====================

# Путь к JSON-ключу сервисного аккаунта
CREDENTIALS_FILE = Path(__file__).parent / "google_credentials.json"

# ID таблицы (из URL)
SPREADSHEET_ID = "11V6YBMK7kFglNeyTVgZl8xc5OMTMP0oy6hQ-IfuMUyI"

# Название листа
SHEET_NAME = "Лист1"

# DATABASE_URL — берём из переменной окружения или вписываем напрямую
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://user:password@localhost/dbname")

# ===================== ПОДКЛЮЧЕНИЕ К SHEETS =====================

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(SHEET_NAME)


# ===================== ЗАГОЛОВКИ =====================

HEADERS = [
    "📅 Дата",
    "💰 Выручка ₽ (факт)",
    "⭐️ Выручка Stars (факт)",
    "📊 Транзакций всего",
    "🆕 Первых покупок",
    "🔄 Повторных покупок",
    "💵 Средний чек ₽",
    "👥 Новых юзеров",
    "⚡️ Активных (DAU)",
    "🛒 Купило всего",
    "📈 CR %",
    "🍌 Бананов потрачено",
    "🎁 Бананов выдано",
    "🛍 Бананов куплено",
]

PLAN_HEADERS = [
    "📋 План выручка ₽",
    "📋 План новых юзеров",
    "📋 План транзакций",
    "📊 Факт/План %",
    "💬 Комментарий",
]


def ensure_headers(sheet):
    """Создаёт заголовки если лист пустой"""
    existing = sheet.row_values(1)
    if not existing or existing[0] != "📅 Дата":
        all_headers = HEADERS + PLAN_HEADERS
        sheet.update("A1", [all_headers])

        # Форматируем заголовок — жирный
        sheet.format("A1:S1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
        })
        print("✅ Заголовки созданы")
    else:
        print("ℹ️ Заголовки уже есть")


# ===================== ГЛАВНАЯ ЛОГИКА =====================

async def collect_stats(date_from: datetime, date_to: datetime) -> dict:
    """Собирает статистику за период из БД"""
    # Импортируем функцию из твоего проекта
    import sys
    sys.path.insert(0, str(Path(__file__).parent))

    from app.database import async_session
    from app.services.analytics_service import get_analytics_report

    async with async_session() as session:
        data = await get_analytics_report(session, date_from, date_to)

    return data


def find_next_empty_row(sheet) -> int:
    """Находит первую пустую строку"""
    col_a = sheet.col_values(1)
    return len(col_a) + 1


def row_already_exists(sheet, date_str: str) -> bool:
    """Проверяет не записана ли уже эта дата"""
    col_a = sheet.col_values(1)
    return date_str in col_a


async def main():
    # Вьетнамское время UTC+7
    vn_tz = timezone(timedelta(hours=7))
    now_vn = datetime.now(vn_tz)

    # Вчерашний день
    yesterday = now_vn.date() - timedelta(days=1)
    date_from = datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0, tzinfo=vn_tz)
    date_to = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59, tzinfo=vn_tz)
    date_str = yesterday.strftime("%d.%m.%Y")

    print(f"📊 Собираем статистику за {date_str}...")

    # Подключаемся к Sheets
    sheet = get_sheet()
    ensure_headers(sheet)

    # Проверяем дубликат
    if row_already_exists(sheet, date_str):
        print(f"⚠️ Строка за {date_str} уже существует, пропускаем")
        return

    # Собираем данные
    data = await collect_stats(date_from, date_to)

    rev = data["revenue"]
    users = data["users"]
    bananas = data["bananas"]

    total_earned_bananas = (
        bananas["earned_ref"] +
        bananas["earned_sub"] +
        bananas["earned_welcome"]
    )

    # Формируем строку (только факт, план заполняешь сам)
    row = [
        date_str,                           # Дата
        rev["rub_revenue"],                 # Выручка ₽
        rev["stars_revenue"],               # Выручка Stars
        rev["transactions"],                # Транзакций всего
        rev["first_purchases"],             # Первых покупок
        rev["repeat_purchases"],            # Повторных покупок
        rev["avg_check"],                   # Средний чек
        users["new"],                       # Новых юзеров
        users["active"],                    # Активных DAU
        users["total_buyers"],              # Купило всего
        users["conversion_rate"],           # CR %
        bananas["spent"],                   # Бананов потрачено
        total_earned_bananas,               # Бананов выдано
        bananas["purchased"],               # Бананов куплено
        "",                                 # План выручка (заполняешь сам)
        "",                                 # План новых юзеров (заполняешь сам)
        "",                                 # План транзакций (заполняешь сам)
        f'=B{find_next_empty_row(sheet)}/O{find_next_empty_row(sheet)}',  # Факт/План %
        "",                                 # Комментарий
    ]

    next_row = find_next_empty_row(sheet)
    sheet.update(f"A{next_row}", [row])

    # Условное форматирование строки (зелёный если CR > 10%)
    cr = users["conversion_rate"]
    if cr >= 10:
        color = {"red": 0.85, "green": 0.95, "blue": 0.85}  # светло-зелёный
    elif cr >= 5:
        color = {"red": 1.0, "green": 1.0, "blue": 0.85}    # светло-жёлтый
    else:
        color = {"red": 1.0, "green": 0.85, "blue": 0.85}    # светло-красный

    sheet.format(f"A{next_row}:S{next_row}", {
        "backgroundColor": color
    })

    print(f"✅ Строка за {date_str} записана в строку {next_row}")
    print(f"   💰 Выручка: {rev['rub_revenue']:.0f} ₽ | 👥 Новых: {users['new']} | CR: {users['conversion_rate']:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())