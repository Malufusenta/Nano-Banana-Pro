"""
Клавиатуры для preflight проверки
"""
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.services.i18n import t


def get_preflight_kb(model_type: str, ratio: str, quality: str, locale: str):
    """
    Создает клавиатуру для preflight проверки
    """
    # Локальный импорт для избежания циклических зависимостей
    from app.services.generation import calc_cost
    
    builder = InlineKeyboardBuilder()

    if model_type == "pro":
        model_btn = t("generation.preflight.model_pro", locale)
    elif model_type == "nb2":
        model_btn = t("generation.preflight.model_nb2", locale)
    else:
        model_btn = t("generation.preflight.model_standard", locale)

    qual_btn = None
    if model_type == "pro":
        if quality == "4k":
            qual_btn = t("generation.preflight.q_pro_4k", locale)
        elif quality == "2k":
            qual_btn = t("generation.preflight.q_pro_2k", locale)
        else:
            qual_btn = t("generation.preflight.q_pro_hd", locale)
    elif model_type == "nb2":
        if quality == "4k":
            qual_btn = t("generation.preflight.q_nb2_4k", locale)
        elif quality == "2k":
            qual_btn = t("generation.preflight.q_nb2_2k", locale)
        else:
            qual_btn = t("generation.preflight.q_nb2_hd", locale)

    cost = calc_cost(model_type, quality)

    builder.button(text=model_btn, callback_data="pf_toggle_model")
    if model_type == "pro":
        builder.button(text=t("generation.preflight.btn_format", locale, ratio=ratio), callback_data="pf_select_ratio")
        if qual_btn:
            builder.button(text=qual_btn, callback_data="pf_toggle_quality")
    else:
        if qual_btn:
            builder.button(text=qual_btn, callback_data="pf_toggle_quality")
        builder.button(text=t("generation.preflight.btn_format", locale, ratio=ratio), callback_data="pf_select_ratio")
    builder.button(text=t("generation.preflight.btn_start", locale, cost=cost), callback_data="pf_start")

    if model_type == "pro":
        builder.adjust(2, 1, 1)
    elif model_type == "nb2":
        builder.adjust(1, 2, 1)
    else:
        builder.adjust(1, 1, 1)

    return builder.as_markup()


def get_ratio_kb(model_type: str = "standard", locale: str = "ru"):
    """
    Создает клавиатуру для выбора соотношения сторон
    """
    builder = InlineKeyboardBuilder()

    if model_type == "nb2":
        ratios = ["1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4", "8:1", "9:16", "16:9", "21:9"]
    else:
        ratios = ["1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "21:9"]

    for r in ratios:
        builder.button(text=r, callback_data=f"set_ratio_{r}")
    builder.button(text=t("shop.back_to_methods", locale), callback_data="pf_back")
    
    if model_type == "nb2":
        builder.adjust(3, 3, 2, 2, 4, 1)
    else:
        builder.adjust(3, 3, 2, 2, 1)
    
    return builder.as_markup()
