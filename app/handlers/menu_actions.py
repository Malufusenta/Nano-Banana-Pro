from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.services.i18n import t, resolve_locale

router = Router()

# Ссылка на твой канал (для примеров)
CHANNEL_LINK = "https://t.me/YourChannel" 
SUPPORT_USERNAME = "@tvoj_username" # Твой юзернейм для связи


def _menu_labels(key: str) -> set[str]:
    return {t(key, "ru"), t(key, "en"), t(key, "es")}

# 1. Кнопка "📸 Примеры работ"
@router.message(F.text.in_(_menu_labels("menu.examples")))
@router.message(Command("examples"))
async def show_examples(message: types.Message):
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    builder = InlineKeyboardBuilder()
    builder.button(text=t("menu.examples_button", locale), url=CHANNEL_LINK)
    await message.answer(t("menu.examples_title", locale), parse_mode="HTML", reply_markup=builder.as_markup())

# 2. Кнопка "ℹ️ Что умеет бот?"
@router.message(F.text.in_(_menu_labels("menu.what_can_bot")))
@router.message(Command("helpinfo"))
async def show_info(message: types.Message):
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    await message.answer(t("menu.info_text", locale), parse_mode="HTML")

# 3. Кнопка "📚 Помощь" (или если нажали /help)
# Мы ловим и текст кнопки, и команду /help
@router.message(F.text.in_(_menu_labels("menu.help")))
@router.message(Command("help"))
async def show_help(message: types.Message):
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    await message.answer(
        t("menu.help_text", locale, support=SUPPORT_USERNAME),
        parse_mode="HTML"
    )