"""
Калькулятор стоимости генерации изображений
"""
from app import config
from app.services.kie_pricing import get_kie_credits as _get_kie_credits


def calc_cost(model_type: str, quality: str) -> int:
    """
    Рассчитывает стоимость генерации в бананах
    
    Args:
        model_type: Тип модели ("pro", "nb2", "standard")
        quality: Качество ("4k", "2k", "hd"/"1k")
        
    Returns:
        Стоимость в бананах
    """
    if model_type == "pro":
        if quality == "4k":
            return config.COST_PRO_4K
        elif quality == "2k":
            return config.COST_PRO_2K
        else:
            return config.COST_PRO_1K
    elif model_type == "nb2":
        if quality == "4k":
            return config.COST_NB2_4K
        elif quality == "2k":
            return config.COST_NB2_2K
        else:
            return config.COST_NB2_1K
    return config.COST_STANDARD


def get_cost_for_model(model_type: str, quality: str = "hd") -> int:
    """
    Обертка над calc_cost с дефолтным качеством
    Используется в местах где качество не критично
    """
    return calc_cost(model_type, quality)


def get_kie_credits(model_type: str, resolution: str = "1K") -> int:
    """
    Обертка над функцией из kie_pricing для единообразия API
    """
    return _get_kie_credits(model_type, resolution)
