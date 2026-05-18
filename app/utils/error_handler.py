"""
Обработчик ошибок генерации
"""
from aiogram import Bot, types
from app.services.i18n import t
from app.database import async_session
from app.services.user_service import admin_change_balance
from app.services.admin_logger import log_error, log_banana_refund, log_security_ban


def get_user_friendly_error_message(err_msg: str, locale: str) -> str:
    """
    Преобразует техническую ошибку в понятное пользователю сообщение
    
    Args:
        err_msg: Сообщение об ошибке (lowercase)
        locale: Локаль пользователя
        
    Returns:
        Локализованное сообщение для пользователя
    """
    err_msg_lower = err_msg.lower()
    
    # --- ГРУППА 1: Цензура и контент ---
    if any(x in err_msg_lower for x in [
        "sensitive", "nsfw", "safety", "banned", "content found", 
        "violated", "policy", "prohibited"
    ]):
        return t("generation.err.security", locale)
    
    # --- ГРУППА 2: Ошибки ввода пользователя (422) ---
    if "422" in err_msg_lower or "validation error" in err_msg_lower:
        return t("generation.err.validation", locale)
    
    # --- ГРУППА 2.5: Специфичная ошибка Gemini ---
    if "gemini could not generate" in err_msg_lower or "different prompt" in err_msg_lower:
        return t("generation.err.gemini", locale)
    
    # --- ГРУППА 2.6: Публичная личность (Kie REJECT specific) ---
    if (
        "kie reject" in err_msg_lower
        and "request blocked" in err_msg_lower
        and ("prominent public figure" in err_msg_lower or "public figure" in err_msg_lower)
    ):
        return t("generation.err.public_figure", locale)
    
    # --- ГРУППА 2.7: Отказ провайдера (Kie REJECT общий) ---
    if "kie reject" in err_msg_lower or "failed to generate image" in err_msg_lower:
        return t("generation.err.kie_reject", locale)
    
    # --- ГРУППА 2.8: Протухшая ссылка на файл в Telegram ---
    if "api.telegram.org/file/" in err_msg_lower and "404" in err_msg_lower:
        return t("generation.err.telegram_expired", locale)
    
    # --- ГРУППА 2.9: Мягкий отказ нейросети ---
    if any(x in err_msg_lower for x in [
        "unable to help you with that", "对不起", "generation failed: sorry"
    ]):
        return t("generation.err.copyright", locale)
    
    # --- ГРУППА 3: Временные проблемы на сервере ---
    if any(x in err_msg_lower for x in [
        "429", "455", "500", "501", "502", "503", 
        "internal", "reject", "timeout", "busy", "queue"
    ]):
        return t("generation.err.server_busy", locale)
    
    # --- ГРУППА 4: Критические ошибки ---
    if any(x in err_msg_lower for x in [
        "401", "402", "404", "505", "unauthorized", "insufficient credits"
    ]):
        return t("generation.err.maintenance", locale)
    
    # --- ГРУППА 5: Всё остальное ---
    return t("generation.err.unknown", locale)


async def handle_generation_error(
    bot: Bot,
    user_id: int,
    username: str | None,
    prompt: str,
    cost: int,
    error: Exception,
    locale: str,
    wait_message: types.Message | None = None,
    reply_message: types.Message | None = None,
) -> None:
    """
    Универсальная обработка ошибок генерации
    
    Выполняет:
    1. Логирование ошибки
    2. Возврат баланса
    3. Отправку понятного сообщения пользователю
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        username: Username пользователя (может быть None)
        prompt: Промпт, который вызвал ошибку
        cost: Стоимость генерации (для возврата)
        error: Исключение, которое произошло
        locale: Локаль пользователя
        wait_message: Сообщение "Генерирую...", которое можно отредактировать
        reply_message: Сообщение для ответа (если wait_message нет)
    """
    # 1. Логируем ошибку
    print(f"❌ Ошибка генерации: {error}")
    
    error_text = str(error)
    await log_error(
        bot,
        user_id,
        username,
        prompt,
        error_text=f"CRASH: {error_text[:100]}"
    )
    
    # 2. Возвращаем баланс
    async with async_session() as session:
        await admin_change_balance(session, user_id, cost)
    
    # Логируем возврат
    await log_banana_refund(
        bot, 
        user_id, 
        username, 
        cost, 
        f"Ошибка генерации: {error_text[:50]}"
    )
    
    # 3. Специальная логика для NSFW ошибок
    err_msg_lower = error_text.lower()
    if any(x in err_msg_lower for x in [
        "sensitive", "nsfw", "safety", "banned", "content found", 
        "violated", "policy", "prohibited"
    ]):
        await log_security_ban(bot, user_id, username, prompt, source="API Filter")
    
    # 4. Получаем понятное сообщение для пользователя
    user_friendly_message = get_user_friendly_error_message(error_text, locale)
    final_text = user_friendly_message + t("generation.msg.refund_footer", locale, cost=cost)
    
    # 5. Отправляем пользователю
    try:
        if wait_message:
            await wait_message.edit_text(final_text, parse_mode="HTML")
        elif reply_message:
            await reply_message.answer(final_text, parse_mode="HTML")
    except Exception as send_error:
        # Если не удалось отредактировать/отправить, пытаемся отправить новое сообщение
        print(f"⚠️ Не удалось отправить сообщение об ошибке: {send_error}")
        try:
            if reply_message:
                await reply_message.answer(final_text, parse_mode="HTML")
        except:
            pass


async def handle_null_result_error(
    bot: Bot,
    user_id: int,
    username: str | None,
    prompt: str,
    cost: int,
    locale: str,
    wait_message: types.Message | None = None,
    reply_message: types.Message | None = None,
) -> None:
    """
    Обработка ситуации когда API вернул NULL
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        username: Username пользователя
        prompt: Промпт генерации
        cost: Стоимость (для возврата)
        locale: Локаль пользователя
        wait_message: Сообщение для редактирования
        reply_message: Сообщение для ответа
    """
    print("❌ API вернул NULL")
    
    # Логируем
    await log_error(
        bot,
        user_id,
        username,
        prompt,
        error_text="API returned NULL (Blocked?)"
    )
    
    # Возвращаем деньги
    async with async_session() as session:
        await admin_change_balance(session, user_id, cost)
    
    await log_banana_refund(
        bot, 
        user_id, 
        username, 
        cost, 
        "API вернул NULL (Blocked?)"
    )
    
    # Отправляем сообщение
    error_text = t("generation.msg.gen_error_null", locale, cost=cost)
    
    try:
        if wait_message:
            await wait_message.edit_text(error_text, parse_mode="HTML")
        elif reply_message:
            await reply_message.answer(error_text, parse_mode="HTML")
    except:
        if reply_message:
            try:
                await reply_message.answer(error_text, parse_mode="HTML")
            except:
                pass
