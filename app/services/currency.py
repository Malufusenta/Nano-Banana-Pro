import aiohttp


async def get_usd_rate() -> float:
    """Получает актуальный курс USD/RUB"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                data = await resp.json()
                return float(data["usd"]["rub"])
    except Exception:
        return 90.0  # fallback если API недоступен
