# app/services/yandex_metrica.py
"""
Сервис для отправки конверсий в Яндекс.Метрику
"""
import aiohttp
import asyncio
import logging
from datetime import datetime
from typing import Optional
import ssl
import certifi
from io import StringIO

logger = logging.getLogger(__name__)


class YandexMetricaService:
    """Класс для работы с API Яндекс.Метрики"""
    
    def __init__(self, counter_id: str, token: str, enabled: bool = True):
        """
        Args:
            counter_id: ID счетчика Яндекс.Метрики
            token: OAuth токен для API
            enabled: Включена ли отправка
        """
        self.counter_id = counter_id
        self.token = token
        self.enabled = enabled
        self.base_url = "https://api-metrika.yandex.net"
    
    async def send_purchase_conversion(
        self,
        client_id: str,
        order_id: int,
        revenue: float,
        tariff_name: str = None
    ) -> bool:
        """
        Отправка конверсии покупки в Яндекс.Метрику через CSV
        
        Args:
            client_id: Yandex ClientID (_ym_uid) пользователя
            order_id: ID покупки из БД
            revenue: Сумма покупки в рублях
            tariff_name: Название тарифа (опционально)
        
        Returns:
            bool: True если успешно отправлено
        """
        
        if not self.enabled:
            logger.info(f"⏭️ Отправка в Метрику отключена (test mode)")
            return True
        
        if not client_id:
            logger.warning(f"⚠️ ClientID отсутствует для order {order_id}")
            return False
        
        url = f"{self.base_url}/management/v1/counter/{self.counter_id}/offline_conversions/upload"
        
        # Формат даты: Unix timestamp
        timestamp = int(datetime.now().timestamp())
        
        # Создаем CSV в памяти
        # Формат: ClientId,Target,DateTime,Price,Currency
        csv_content = f"ClientId,Target,DateTime,Price,Currency\n"
        csv_content += f"{client_id},PURCHASE,{timestamp},{revenue},RUB\n"
        
        # Создаем form data для загрузки файла
        form_data = aiohttp.FormData()
        form_data.add_field(
            'file',
            csv_content,
            filename='conversions.csv',
            content_type='text/csv'
        )
        
        headers = {
            "Authorization": f"OAuth {self.token}"
        }
        
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
                async with session.post(url, data=form_data, headers=headers, timeout=10) as response:
                    status = response.status
                    result_text = await response.text()
                    
                    if status == 200:
                        logger.info(
                            f"✅ Конверсия отправлена: "
                            f"order={order_id}, cid={client_id[:8]}..., sum={revenue}₽"
                        )
                        return True
                    else:
                        logger.error(
                            f"❌ Ошибка Метрики: status={status}, order={order_id}, "
                            f"response={result_text}"
                        )
                        return False
                        
        except asyncio.TimeoutError:
            logger.error(f"⏱️ Timeout при отправке: order={order_id}")
            return False
        except Exception as e:
            logger.error(f"❌ Исключение при отправке: order={order_id}, error={e}")
            return False
    
    async def send_bot_start_event(self, client_id: str):
        """
        Отправка цели BOT_START при переходе в бота
        """
        if not self.enabled:
            logger.info(f"⏭️ BOT_START: Метрика отключена (test mode)")
            return
        
        if not client_id:
            logger.warning(f"⚠️ BOT_START: ClientID отсутствует")
            return
        
        try:
            # Формируем параметры запроса
            params = {
                'wmode': 7,
                'page-url': 'https://t.me/nan0banana_bot/start',
                'client-id': client_id,
                'goal-id': 'BOT_START'
            }
            
            url = f"https://mc.yandex.ru/watch/{self.counter_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=3)) as response:
                    if response.status == 200:
                        logger.info(f"✅ BOT_START отправлен для ClientID: {client_id}")
                    else:
                        logger.warning(f"⚠️ BOT_START: HTTP {response.status}")
        
        except Exception as e:
            logger.error(f"❌ Ошибка отправки BOT_START: {e}")


# Глобальный экземпляр
metrica_service: Optional[YandexMetricaService] = None


def init_metrica_service(counter_id: str, token: str, enabled: bool = True):
    """Инициализация сервиса"""
    global metrica_service
    metrica_service = YandexMetricaService(counter_id, token, enabled)
    logger.info(f"📊 Yandex Metrica инициализирован (enabled={enabled})")