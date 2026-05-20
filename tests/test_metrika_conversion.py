"""
Тесты для интеграции ценности конверсии в Яндекс.Метрику:
  - stars_to_rub / usd_to_rub
  - send_purchase_conversion (ev, cu, params в запросе)
  - _run_first_purchase_metrika_effects (маршрутизация валют, условия срабатывания)
"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_db_user(*, yandex_client_id: str | None = "1234567890123456") -> MagicMock:
    u = MagicMock()
    u.telegram_id = 999
    u.yandex_client_id = yandex_client_id
    return u


# ---------------------------------------------------------------------------
# 1. currency.py — stars_to_rub / usd_to_rub
# ---------------------------------------------------------------------------

class CurrencyConversionTest(unittest.IsolatedAsyncioTestCase):

    async def test_stars_to_rub_uses_rate(self):
        from app.services.currency import stars_to_rub
        with patch("app.services.currency.get_usd_rate", AsyncMock(return_value=90.0)):
            result = await stars_to_rub(100)
        # 100 * 0.013 * 90 = 117.0
        self.assertAlmostEqual(result, 117.0, places=2)

    async def test_stars_to_rub_fallback_rate(self):
        from app.services.currency import stars_to_rub
        with patch("app.services.currency.get_usd_rate", AsyncMock(return_value=90.0)):
            result = await stars_to_rub(0)
        self.assertEqual(result, 0.0)

    async def test_usd_to_rub_uses_rate(self):
        from app.services.currency import usd_to_rub
        with patch("app.services.currency.get_usd_rate", AsyncMock(return_value=90.0)):
            result = await usd_to_rub(5.0)
        self.assertAlmostEqual(result, 450.0, places=2)

    async def test_usd_to_rub_rounds(self):
        from app.services.currency import usd_to_rub
        with patch("app.services.currency.get_usd_rate", AsyncMock(return_value=91.333)):
            result = await usd_to_rub(1.0)
        self.assertEqual(result, round(91.333, 2))


# ---------------------------------------------------------------------------
# 2. send_purchase_conversion — ev, cu, params передаются в _send_collect_event
# ---------------------------------------------------------------------------

class SendPurchaseConversionTest(unittest.IsolatedAsyncioTestCase):

    def _make_service(self):
        from app.services.yandex_metrica import YandexMetricaService
        svc = YandexMetricaService(
            counter_id="12345",
            token="tok",
            enabled=True,
            ms_token="ms-token",
        )
        return svc

    async def test_ev_and_cu_passed_to_collect(self):
        svc = self._make_service()
        captured = {}

        async def fake_collect(client_id, ea, *, dl, extra_params=None, log_suffix=""):
            captured["extra_params"] = extra_params
            return True

        svc._send_collect_event = fake_collect
        await svc.send_purchase_conversion(
            client_id="cid123",
            order_id=42,
            revenue=999.0,
            currency="RUB",
            purchase_id=42,
        )
        ep = captured["extra_params"]
        self.assertIsNotNone(ep)
        self.assertEqual(ep.get("ev"), "999.0")
        self.assertEqual(ep.get("cu"), "RUB")

    async def test_params_contains_purchase_id(self):
        svc = self._make_service()
        captured = {}

        async def fake_collect(client_id, ea, *, dl, extra_params=None, log_suffix=""):
            captured["extra_params"] = extra_params
            return True

        svc._send_collect_event = fake_collect
        await svc.send_purchase_conversion(
            client_id="cid123",
            order_id=77,
            revenue=100.0,
            purchase_id=77,
        )
        params_raw = captured["extra_params"].get("params")
        self.assertIsNotNone(params_raw)
        params = json.loads(params_raw)
        self.assertEqual(params["purchase_id"], 77)

    async def test_zero_revenue_no_ev(self):
        """Если revenue=0.0, ev не должен попасть в запрос."""
        svc = self._make_service()
        captured = {}

        async def fake_collect(client_id, ea, *, dl, extra_params=None, log_suffix=""):
            captured["extra_params"] = extra_params
            return True

        svc._send_collect_event = fake_collect
        await svc.send_purchase_conversion(
            client_id="cid123",
            order_id=1,
            revenue=0.0,
        )
        ep = captured.get("extra_params") or {}
        self.assertNotIn("ev", ep)

    async def test_disabled_service_returns_true(self):
        from app.services.yandex_metrica import YandexMetricaService
        svc = YandexMetricaService("1", "t", enabled=False)
        result = await svc.send_purchase_conversion("cid", 1, revenue=100.0)
        self.assertTrue(result)

    async def test_missing_client_id_returns_false(self):
        svc = self._make_service()
        result = await svc.send_purchase_conversion("", 1, revenue=100.0)
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# 3. _run_first_purchase_metrika_effects — маршрутизация валют и условия
# ---------------------------------------------------------------------------

class RunFirstPurchaseMetrikaTest(unittest.IsolatedAsyncioTestCase):

    async def _call(self, *, db_user, purchase_id=1, original_revenue=100.0,
                    currency="RUB", payment_system="Test", is_first_purchase=True,
                    fake_metrica_service=None):
        import app.services.yandex_metrica as mod
        mock_svc = fake_metrica_service or AsyncMock()
        mock_svc.send_purchase_conversion = AsyncMock(return_value=True)
        with patch.object(mod, "metrica_service", mock_svc):
            await mod._run_first_purchase_metrika_effects(
                db_user,
                purchase_id=purchase_id,
                original_revenue=original_revenue,
                currency=currency,
                payment_system=payment_system,
                is_first_purchase=is_first_purchase,
            )
        return mock_svc

    async def test_rub_passthrough(self):
        user = _mock_db_user()
        svc = await self._call(db_user=user, original_revenue=500.0, currency="RUB")
        svc.send_purchase_conversion.assert_awaited_once()
        _, kwargs = svc.send_purchase_conversion.call_args
        self.assertAlmostEqual(kwargs["revenue"], 500.0)
        self.assertEqual(kwargs["currency"], "RUB")

    async def test_xtr_converts_to_rub(self):
        user = _mock_db_user()
        with patch("app.services.currency.get_usd_rate", AsyncMock(return_value=90.0)):
            svc = await self._call(db_user=user, original_revenue=100, currency="XTR")
        _, kwargs = svc.send_purchase_conversion.call_args
        # 100 * 0.013 * 90 = 117.0
        self.assertAlmostEqual(kwargs["revenue"], 117.0, places=1)
        self.assertEqual(kwargs["currency"], "RUB")

    async def test_usdt_converts_to_rub(self):
        user = _mock_db_user()
        with patch("app.services.currency.get_usd_rate", AsyncMock(return_value=90.0)):
            svc = await self._call(db_user=user, original_revenue=5.0, currency="USDT")
        _, kwargs = svc.send_purchase_conversion.call_args
        self.assertAlmostEqual(kwargs["revenue"], 450.0, places=1)

    async def test_usd_converts_to_rub(self):
        user = _mock_db_user()
        with patch("app.services.currency.get_usd_rate", AsyncMock(return_value=90.0)):
            svc = await self._call(db_user=user, original_revenue=2.0, currency="USD")
        _, kwargs = svc.send_purchase_conversion.call_args
        self.assertAlmostEqual(kwargs["revenue"], 180.0, places=1)

    async def test_ton_skipped_with_warning(self):
        """TON должен логировать warning и НЕ вызывать send_purchase_conversion."""
        user = _mock_db_user()
        import app.services.yandex_metrica as mod
        mock_svc = MagicMock()
        mock_svc.send_purchase_conversion = AsyncMock()
        with patch.object(mod, "metrica_service", mock_svc):
            with self.assertLogs("app.services.yandex_metrica", level="WARNING") as cm:
                await mod._run_first_purchase_metrika_effects(
                    user,
                    purchase_id=1,
                    original_revenue=10.0,
                    currency="TON",
                    payment_system="Crypto Pay TON",
                    is_first_purchase=True,
                )
        mock_svc.send_purchase_conversion.assert_not_awaited()
        self.assertTrue(any("TON" in line for line in cm.output))

    async def test_not_first_purchase_skipped(self):
        """Повторная покупка — Метрика не вызывается."""
        user = _mock_db_user()
        svc = await self._call(db_user=user, is_first_purchase=False)
        svc.send_purchase_conversion.assert_not_awaited()

    async def test_no_yandex_client_id_skipped(self):
        """Нет ClientID — Метрика не вызывается."""
        user = _mock_db_user(yandex_client_id=None)
        svc = await self._call(db_user=user)
        svc.send_purchase_conversion.assert_not_awaited()

    async def test_none_db_user_skipped(self):
        """db_user=None — не падает, просто ничего не делает."""
        import app.services.yandex_metrica as mod
        mock_svc = AsyncMock()
        mock_svc.send_purchase_conversion = AsyncMock()
        with patch.object(mod, "metrica_service", mock_svc):
            await mod._run_first_purchase_metrika_effects(
                None,
                purchase_id=1,
                original_revenue=100.0,
                currency="RUB",
                payment_system="Test",
                is_first_purchase=True,
            )
        mock_svc.send_purchase_conversion.assert_not_awaited()

    async def test_purchase_id_forwarded(self):
        """purchase_id должен уйти в send_purchase_conversion."""
        user = _mock_db_user()
        svc = await self._call(db_user=user, purchase_id=42, currency="RUB")
        _, kwargs = svc.send_purchase_conversion.call_args
        self.assertEqual(kwargs["purchase_id"], 42)
        self.assertEqual(kwargs["order_id"], 42)


if __name__ == "__main__":
    unittest.main()
