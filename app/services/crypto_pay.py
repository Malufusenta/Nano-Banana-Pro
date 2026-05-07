from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app import config


class CryptoPayService:
    def __init__(self):
        self.base_url = config.CRYPTO_PAY_API_BASE.rstrip("/")
        self.token = config.CRYPTO_PAY_TOKEN
        self._headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json",
        }

    def verify_webhook_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        if not signature_header or not self.token:
            return False
        secret = hashlib.sha256(self.token.encode("utf-8")).digest()
        expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header.strip())

    async def create_invoice(
        self,
        *,
        telegram_user_id: int,
        package_key: str,
        bananas: int,
        fiat_amount_usd: str,
        description: str,
        accepted_assets: str = "USDT,TON",
        expires_in: int = 3600,
    ) -> dict[str, Any]:
        payload_obj = {"user_id": telegram_user_id, "pkg": package_key, "bananas": bananas}
        payload_str = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False)
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.base_url}/createInvoice",
                headers=self._headers,
                json={
                    "currency_type": "fiat",
                    "fiat": "USD",
                    "amount": fiat_amount_usd,
                    "accepted_assets": accepted_assets,
                    "payload": payload_str,
                    "description": description,
                    "expires_in": expires_in,
                },
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Crypto Pay createInvoice failed: {data}")
            return data["result"]


crypto_pay = CryptoPayService()
