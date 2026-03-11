import aiohttp
import ssl
import certifi

# Стоимость в кредитах kie.ai (реальные цены с сайта)
KIE_CREDITS = {
    "standard": 4,    # ~$0.02
    "pro_1k": 18,     # ~$0.09
    "pro_2k": 18,     # ~$0.09
    "pro_4k": 24,     # ~$0.12
    "nb2_1k": 8,      # ~$0.04
    "nb2_2k": 12,     # ~$0.06
    "nb2_4k": 18,     # ~$0.09
    "video": 56,      # ~$0.28
}

KIE_CREDIT_TO_USD = 0.00450  # $0.005 с учетом 10% бонусных кредитов (тариф $500)

def get_kie_credits(model_type: str, resolution: str = "1K") -> int:
    if model_type == "standard":
        return KIE_CREDITS["standard"]
    elif model_type == "nb2":
        return KIE_CREDITS.get(f"nb2_{resolution.lower()}", 8)
    elif model_type == "pro":
        return KIE_CREDITS.get(f"pro_{resolution.lower()}", 18)
    elif model_type == "video":
        return KIE_CREDITS["video"]
    return 4

def credits_to_usd(credits: int) -> float:
    return round(credits * KIE_CREDIT_TO_USD, 4)

async def get_kie_balance() -> dict:
    """Получает текущий баланс кредитов kie.ai"""
    from app import config
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                "https://api.kie.ai/api/v1/chat/credit",
                headers={"Authorization": f"Bearer {config.KIE_API_KEY}"}
            ) as resp:
                data = await resp.json()
                raw = data.get("data", 0)
                credits = raw if isinstance(raw, (int, float)) else raw.get("credit", 0) if isinstance(raw, dict) else 0
                return {
                    'credits': credits,
                    'usd': round(credits * KIE_CREDIT_TO_USD, 2)
                }
    except Exception as e:
        return {'credits': 0, 'usd': 0, 'error': str(e)}