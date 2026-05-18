"""
Клавиатуры для результатов генерации
"""
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.services.i18n import t
from app import config


def get_cancel_kb(locale: str = "en"):
    """
    Создает клавиатуру с кнопкой отмены
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t("common.cancel_button", locale), callback_data="cancel_wizard")
    return builder.as_markup()


def get_result_kb(
    db_message_id: int,
    is_pro: bool,
    cost: int,
    is_nb2: bool = False,
    locale: str = "en",
):
    """
    Создает клавиатуру для результата генерации
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t("generation.result.btn_reroll", locale, cost=cost),
        callback_data=f"reroll_{db_message_id}",
    )
    builder.button(
        text=t("generation.result.btn_edit", locale, cost=cost),
        callback_data=f"edit_{db_message_id}",
    )
    builder.button(
        text=t("generation.video.btn_animate", locale, cost=config.COST_VIDEO),
        callback_data=f"animate_{db_message_id}",
    )
    if is_pro or is_nb2:
        builder.button(
            text=t("generation.result.btn_download_lossless", locale),
            callback_data=f"download_{db_message_id}",
        )
    builder.adjust(2, 1, 1) if (is_pro or is_nb2) else builder.adjust(2, 1)
    return builder.as_markup()
