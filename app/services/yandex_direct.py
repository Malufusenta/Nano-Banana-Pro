"""
Сервис для получения статистики расходов из Яндекс.Директ
"""
import aiohttp
import ssl
import certifi
import logging
from datetime import date

logger = logging.getLogger(__name__)

DIRECT_API_URL = "https://api.direct.yandex.com/json/v5/reports"

async def get_direct_spending(token: str, date_from: date, date_to: date) -> dict:
    """
    Получает расходы из Яндекс.Директ за период.
    Возвращает общую сумму и разбивку по кампаниям.
    НДС 20% добавляется сверху.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Language": "ru",
        "Content-Type": "application/json",
        "returnMoneyInMicros": "false",
        "skipReportHeader": "true",
        "skipColumnHeader": "true",
        "skipReportSummary": "true",
    }

    body = {
        "params": {
            "SelectionCriteria": {
                "DateFrom": date_from.strftime("%Y-%m-%d"),
                "DateTo": date_to.strftime("%Y-%m-%d"),
            },
            "FieldNames": ["CampaignName", "Cost"],
            "ReportName": f"NanoBanana_{date_from}_{date_to}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "NO",
            "IncludeDiscount": "NO",
        }
    }

    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                DIRECT_API_URL,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status not in (200, 201, 202):
                    text = await resp.text()
                    logger.error(f"Директ API ошибка: {resp.status} - {text}")
                    return {'error': f"HTTP {resp.status}", 'total': 0, 'campaigns': {}}

                text = await resp.text()

        # Парсим TSV
        campaigns = {}
        total = 0.0
        VAT = 1.20  # +20% НДС

        for line in text.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[0].strip().replace('\xa0', ' ')
            try:
                cost = float(parts[1].strip()) * VAT
                cost = round(cost, 2)
                campaigns[name] = cost
                total += cost
            except ValueError:
                continue

        return {
            'total': round(total, 2),
            'campaigns': campaigns,
            'error': None
        }

    except Exception as e:
        logger.error(f"Директ API исключение: {e}")
        return {'error': str(e), 'total': 0, 'campaigns': {}}