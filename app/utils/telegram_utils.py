"""
Утилиты для работы с Telegram API
"""
from aiogram import Bot, types


async def get_photo_url(bot: Bot, file_id: str) -> str:
    """Получает URL фото"""
    if not file_id:
        return None
    file_info = await bot.get_file(file_id)
    return f"https://api.telegram.org/file/bot{bot.token}/{file_info.file_path}"


def is_image_document(message: types.Message) -> bool:
    """
    Проверяет, является ли документ изображением
    """
    d = message.document
    if not d:
        return False
    mt = (d.mime_type or "").lower()
    if mt.startswith("image/"):
        return True
    fn = (d.file_name or "").lower()
    return any(fn.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"))
