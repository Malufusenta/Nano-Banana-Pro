import aiohttp


async def get_usd_rate() -> float:
    """Получает актуальный курс USD/RUB по данным ЦБ РФ"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://www.cbr-xml-daily.ru/daily_json.js",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                data = await resp.json(content_type=None)
                return float(data["Valute"]["USD"]["Value"])
    except Exception:
        return 90.0  # fallback если API недоступен
