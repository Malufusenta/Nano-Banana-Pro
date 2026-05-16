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

logger = logging.getLogger(__name__)

TELEGRAM_START_DL = "https://t.me/nan0banana_bot/start"
BOT_START_GOAL_ID = "BOT_START"
COLLECT_URL = "https://mc.yandex.ru/collect/"


class YandexMetricaService:
    """Класс для работы с API Яндекс.Метрики"""

    def __init__(
        self,
        counter_id: str,
        token: str,
        enabled: bool = True,
        bot_start_target: str = "",
        ms_token: str = "",
    ):
        """
        Args:
            counter_id: ID счетчика Яндекс.Метрики
            token: OAuth токен для API (офлайн-конверсии)
            enabled: Включена ли отправка
            bot_start_target: Target для офлайн CSV (опционально)
            ms_token: Токен Measurement Protocol для /collect/
        """
        self.counter_id = counter_id
        self.token = token
        self.enabled = enabled
        self.bot_start_target = (bot_start_target or "").strip()
        self.ms_token = (ms_token or "").strip()
        self.base_url = "https://api-metrika.yandex.net"

    async def _post_offline_csv(self, csv_content: str, log_prefix: str) -> bool:
        """POST /management/v1/counter/{id}/offline_conversions/upload"""
        url = f"{self.base_url}/management/v1/counter/{self.counter_id}/offline_conversions/upload"
        form_data = aiohttp.FormData()
        form_data.add_field(
            "file",
            csv_content,
            filename="conversions.csv",
            content_type="text/csv",
        )
        headers = {"Authorization": f"OAuth {self.token}"}
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl_context)
            ) as session:
                async with session.post(
                    url, data=form_data, headers=headers, timeout=10
                ) as response:
                    status = response.status
                    result_text = await response.text()
                    if status == 200:
                        return True
                    logger.error(
                        f"❌ {log_prefix}: Метрика status={status}, response={result_text[:500]}"
                    )
                    return False
        except asyncio.TimeoutError:
            logger.error(f"⏱️ {log_prefix}: timeout при загрузке офлайн-конверсии")
            return False
        except Exception as e:
            logger.error(f"❌ {log_prefix}: ошибка загрузки: {e}")
            return False

    async def _send_bot_start_collect_goal(self, client_id: str) -> bool:
        """JS-цель BOT_START через Measurement Protocol (/collect/)."""
        if not self.ms_token:
            logger.warning(
                "⚠️ BOT_START collect: YANDEX_METRICA_MS_TOKEN не задан — событие не отправлено"
            )
            return False

        params = {
            "tid": self.counter_id,
            "cid": client_id,
            "t": "event",
            "ea": BOT_START_GOAL_ID,
            "ms": self.ms_token,
            "et": str(int(datetime.now().timestamp())),
            "dl": TELEGRAM_START_DL,
        }
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl_context)
            ) as session:
                async with session.get(
                    COLLECT_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    body = await response.text()
                    if response.status in (200, 204):
                        logger.info(
                            f"✅ BOT_START collect (Measurement Protocol): "
                            f"ea={BOT_START_GOAL_ID}, tid={self.counter_id}, "
                            f"cid={client_id[:12]}..., status={response.status}"
                        )
                        return True
                    logger.warning(
                        f"⚠️ BOT_START collect: HTTP {response.status}, "
                        f"cid={client_id[:12]}..., response={body[:300]}"
                    )
                    return False
        except asyncio.TimeoutError:
            logger.error(f"⏱️ BOT_START collect: timeout, cid={client_id[:12]}...")
            return False
        except Exception as e:
            logger.error(f"❌ BOT_START collect: {e}, cid={client_id[:12]}...")
            return False

    async def send_purchase_conversion(
        self,
        client_id: str,
        order_id: int,
        revenue: float,
        tariff_name: str = None,
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
            logger.info("⏭️ Отправка в Метрику отключена (test mode)")
            return True

        if not client_id:
            logger.warning(f"⚠️ ClientID отсутствует для order {order_id}")
            return False

        timestamp = int(datetime.now().timestamp())
        csv_content = "ClientId,Target,DateTime,Price,Currency\n"
        csv_content += f"{client_id},PURCHASE,{timestamp},{revenue},RUB\n"

        ok = await self._post_offline_csv(csv_content, f"PURCHASE order={order_id}")
        if ok:
            logger.info(
                f"✅ Конверсия отправлена: order={order_id}, cid={client_id[:8]}..., sum={revenue}₽"
            )
        return ok

    async def send_bot_start_event(self, client_id: str) -> bool:
        """
        Старт в боте: Measurement Protocol (/collect/, ea=BOT_START);
        опционально офлайн CSV (Target = bot_start_target) при наличии OAuth-токена.
        """
        if not self.enabled:
            logger.info("⏭️ BOT_START: Метрика отключена (test mode)")
            return True

        if not client_id:
            logger.warning("⚠️ BOT_START: ClientID отсутствует")
            return False

        offline_ok = True
        if self.bot_start_target and self.token:
            timestamp = int(datetime.now().timestamp())
            csv_content = "ClientId,Target,DateTime\n"
            csv_content += f"{client_id},{self.bot_start_target},{timestamp}\n"
            offline_ok = await self._post_offline_csv(csv_content, "BOT_START offline")
            if offline_ok:
                logger.info(
                    f"✅ BOT_START офлайн: target={self.bot_start_target}, cid={client_id[:12]}..."
                )
        elif self.bot_start_target and not self.token:
            logger.warning(
                "⚠️ BOT_START: пустой YANDEX_METRICA_TOKEN — офлайн пропущена, только collect"
            )
            offline_ok = False

        collect_ok = await self._send_bot_start_collect_goal(client_id)

        need_offline = bool(self.bot_start_target and self.token)
        return collect_ok and (not need_offline or offline_ok)


# Глобальный экземпляр
metrica_service: Optional[YandexMetricaService] = None


def init_metrica_service(
    counter_id: str,
    token: str,
    enabled: bool = True,
    bot_start_target: str = "",
    ms_token: str = "",
):
    """Инициализация сервиса"""
    global metrica_service
    metrica_service = YandexMetricaService(
        counter_id,
        token,
        enabled,
        bot_start_target=bot_start_target,
        ms_token=ms_token,
    )
    mp_status = "настроен" if (ms_token or "").strip() else "не задан (YANDEX_METRICA_MS_TOKEN)"
    logger.info(
        f"📊 Yandex Metrica инициализирован (enabled={enabled}, collect={mp_status})"
    )
