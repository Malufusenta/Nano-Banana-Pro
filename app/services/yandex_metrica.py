# app/services/yandex_metrica.py
"""
Сервис для отправки конверсий в Яндекс.Метрику
"""
import aiohttp
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional
import ssl
import certifi

logger = logging.getLogger(__name__)

TELEGRAM_START_DL = "https://t.me/nan0banana_bot/start"
TELEGRAM_BOT_DL = "https://t.me/nan0banana_bot"
BOT_START_GOAL_ID = "BOT_START"
PURCHASE_GOAL_ID = "PURCHASE"
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
            token: OAuth токен для API (офлайн BOT_START, опционально)
            enabled: Включена ли отправка
            bot_start_target: Target для офлайн CSV старта (опционально)
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

    async def _send_collect_event(
        self,
        client_id: str,
        ea: str,
        *,
        dl: str = TELEGRAM_BOT_DL,
        extra_params: dict[str, Any] | None = None,
        log_suffix: str = "",
    ) -> bool:
        """Отправка JS-цели через Measurement Protocol (/collect/)."""
        if not self.ms_token:
            logger.warning(
                f"⚠️ {ea} collect: YANDEX_METRICA_MS_TOKEN не задан — событие не отправлено"
            )
            return False

        params: dict[str, str] = {
            "tid": self.counter_id,
            "cid": client_id,
            "t": "event",
            "ea": ea,
            "ms": self.ms_token,
            "et": str(int(datetime.now().timestamp())),
            "dl": dl,
        }
        if extra_params:
            for key, value in extra_params.items():
                if value is not None:
                    params[key] = str(value)

        suffix_part = f", {log_suffix}" if log_suffix else ""
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
                            f"✅ {ea} collect (Measurement Protocol): "
                            f"ea={ea}, tid={self.counter_id}, "
                            f"cid={client_id[:12]}...{suffix_part}, status={response.status}"
                        )
                        return True
                    logger.warning(
                        f"⚠️ {ea} collect: HTTP {response.status}, "
                        f"cid={client_id[:12]}..., response={body[:300]}"
                    )
                    return False
        except asyncio.TimeoutError:
            logger.error(f"⏱️ {ea} collect: timeout, cid={client_id[:12]}...")
            return False
        except Exception as e:
            logger.error(f"❌ {ea} collect: {e}, cid={client_id[:12]}...")
            return False

    async def send_purchase_conversion(
        self,
        client_id: str,
        order_id: int,
        revenue: float = 0.0,
        tariff_name: str = None,
        currency: str = "RUB",
        purchase_id: int = None,
    ) -> bool:
        """
        Отправка конверсии покупки в Яндекс.Метрику через Measurement Protocol.

        Args:
            client_id:   Yandex ClientID (_ym_uid) пользователя
            order_id:    ID покупки из БД (используется, если purchase_id не задан)
            revenue:     Сумма покупки в рублях (передаётся как ценность цели ev)
            tariff_name: Название тарифа (для логов)
            currency:    Валюта (cu); всегда должна быть 'RUB' после конвертации
            purchase_id: ID покупки для трассировки в params и логах
        """
        if not self.enabled:
            logger.info("⏭️ Отправка в Метрику отключена (test mode)")
            return True

        if not client_id:
            logger.warning(f"⚠️ ClientID отсутствует для order {order_id}")
            return False

        pid = purchase_id or order_id
        log_suffix = f"purchase_id={pid}, revenue={revenue}₽, currency={currency}"
        if tariff_name:
            log_suffix += f", tariff={tariff_name}"

        extra: dict[str, Any] = {}
        if revenue:
            extra["ev"] = str(round(revenue, 2))
            extra["cu"] = currency
        if pid:
            extra["params"] = json.dumps({"purchase_id": pid})

        return await self._send_collect_event(
            client_id,
            PURCHASE_GOAL_ID,
            dl=TELEGRAM_BOT_DL,
            extra_params=extra or None,
            log_suffix=log_suffix,
        )

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

        collect_ok = await self._send_collect_event(
            client_id,
            BOT_START_GOAL_ID,
            dl=TELEGRAM_START_DL,
        )

        need_offline = bool(self.bot_start_target and self.token)
        return collect_ok and (not need_offline or offline_ok)


async def _run_first_purchase_metrika_effects(
    db_user,
    *,
    purchase_id: int,
    original_revenue: float,
    currency: str,
    payment_system: str,
    is_first_purchase: bool,
) -> None:
    """
    Отправляет ценность первой покупки в Яндекс.Метрику.

    Конвертирует исходную валюту в RUB и вызывает send_purchase_conversion.
    TON и прочие неизвестные активы не отправляются (warning в лог).
    Безопасен: всё обёрнуто в try/except.
    """
    if not (db_user and getattr(db_user, "yandex_client_id", None) and is_first_purchase):
        return
    if not metrica_service:
        return

    try:
        from app.services.currency import stars_to_rub, usd_to_rub

        currency_upper = (currency or "").upper()
        if currency_upper == "XTR":
            revenue = await stars_to_rub(int(original_revenue))
        elif currency_upper in ("USD", "USDT"):
            revenue = await usd_to_rub(original_revenue)
        elif currency_upper == "TON":
            logger.warning(
                f"⚠️ Metrica: TON конвертация не поддерживается — "
                f"purchase_id={purchase_id}, user={db_user.telegram_id}. Пропускаем."
            )
            return
        else:
            revenue = float(original_revenue)

        await metrica_service.send_purchase_conversion(
            client_id=db_user.yandex_client_id,
            order_id=purchase_id,
            revenue=revenue,
            tariff_name=payment_system,
            currency="RUB",
            purchase_id=purchase_id,
        )
        logger.info(
            f"✅ Metrica PURCHASE: user={db_user.telegram_id}, "
            f"purchase_id={purchase_id}, revenue={revenue}₽, system={payment_system}"
        )
    except Exception as e:
        logger.error(
            f"❌ Metrica first purchase error: user={db_user.telegram_id}, "
            f"purchase_id={purchase_id}, err={e}"
        )


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
