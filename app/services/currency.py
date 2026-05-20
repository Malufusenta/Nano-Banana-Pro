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


async def stars_to_rub(stars: int) -> float:
    """Конвертирует Telegram Stars в рубли (Gross, без вычета 30% комиссии).
    Формула: 1 Stars = $0.013 по курсу ЦБ.
    """
    usd_rate = await get_usd_rate()
    return round(stars * 0.013 * usd_rate, 2)


async def usd_to_rub(usd: float) -> float:
    """Конвертирует USD/USDT в рубли по курсу ЦБ."""
    usd_rate = await get_usd_rate()
    return round(usd * usd_rate, 2)
