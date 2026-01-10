from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
# Теперь этот импорт точно сработает, файлы на месте
from app.services.admin_logger import log_new_user, log_referral
from app.database import async_session
from app.services.user_service import get_user, create_user, admin_change_balance
from app import config
from sqlalchemy import select
from app.models import PostConfig
from app.services.user_service import get_user, create_user, admin_change_balance, track_banana_transaction
from app.database import async_session

router = Router()

WELCOME_PHOTO = "AgACAgIAAxkBAAIGbWky1V4aiUImfckmTzqXjKcykdunAAJqC2sb4L2ZSWGkUXDH06FzAQADAgADeQADNgQ"
CHANNEL_LINK = "https://t.me/nanobanan_promt"

def get_banana_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11: return "банан"
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20): return "банана"
    return "бананов"

def get_main_kb():
    kb = [
        [KeyboardButton(text="✨ Начать творить")],
        [KeyboardButton(text="🍌 Купить бананы"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="📚 Гайд"), KeyboardButton(text="💬 Поддержка")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, input_field_placeholder="Пиши сюда ")

@router.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id
    welcome_bonus = 2
    
    # 1. ЛОВИМ ИСТОЧНИК
    referrer_id = None
    source = None
    args = command.args # Хвостик ссылки

    # 🔥 НОВАЯ ЛОГИКА: Проверяем на post_XX
    is_post_link = False
    post_config = None
    
    if args and args.startswith("post_"):
        is_post_link = True
        # Ищем конфиг в БД
        async with async_session() as session:
            result = await session.execute(
                select(PostConfig).where(PostConfig.config_id == args)
            )
            post_config = result.scalar_one_or_none()
        
        # Если конфиг не найден - показываем ошибку
        if not post_config:
            await message.answer(
                "⚠️ <b>Ссылка устарела или неверна.</b>\n\n"
                "Попробуйте найти свежую ссылку в нашем канале!",
                parse_mode="HTML"
            )
            return
        
        # Увеличиваем счетчик кликов
        async with async_session() as session:
            result = await session.execute(
                select(PostConfig).where(PostConfig.config_id == args)
            )
            config = result.scalar_one_or_none()
            if config:
                config.clicks_count += 1
                await session.commit()
    
    elif args:
        if args.isdigit():
            possible_ref = int(args)
            if possible_ref != user_id:
                referrer_id = possible_ref
                source = "ref_friend"
        else:
            source = args # Например "google" или "yandex"

    async with async_session() as session:
        user = await get_user(session, user_id)
        
        # ЕСЛИ НОВЫЙ ЮЗЕР
        if not user:
            # Передаем source в функцию создания
            await create_user(
                session, 
                telegram_id=user_id, 
                username=message.from_user.username, 
                full_name=message.from_user.full_name, 
                referrer_id=referrer_id,
                source=source if not is_post_link else f"post_{post_config.config_id if post_config else 'unknown'}"
            )

            # Шлем лог админу
            await log_new_user(bot, message.from_user, deep_link=args)

            # Начисляем бонусы
            await admin_change_balance(session, user_id, welcome_bonus)
            await track_banana_transaction(session, user_id, welcome_bonus, "welcome", "Welcome bonus")
            await session.commit()

            if referrer_id:
                try:
                    await admin_change_balance(session, referrer_id, 2)
                    # Трекинг реферального бонуса
                    await track_banana_transaction(session, referrer_id, 2, "earned_ref", f"Referral from {user_id}")
                    await session.commit()
                    
                    # 🆕 ПОЛУЧАЕМ ОБНОВЛЕННЫЙ БАЛАНС
                    referrer = await get_user(session, referrer_id)
                    new_balance = referrer.generations_balance if referrer else 0
                    
                    # 🆕 СОЗДАЕМ КНОПКУ "Пригласить ещё"
                    from aiogram.utils.keyboard import InlineKeyboardBuilder
                    builder = InlineKeyboardBuilder()
                    builder.button(text="🤝 Пригласить ещё", callback_data="goto_free")
                    
                    # 🆕 ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ ПО ТЗ
                    await bot.send_message(
                        referrer_id,
                        f"🥳 Ура! По твоей ссылке пришел друг.\n"
                        f"Баланс пополнен: <b>+2 банана</b> 🍌\n\n"
                        f"Всего на счету: <b>{new_balance}</b>",
                        parse_mode="HTML",
                        reply_markup=builder.as_markup()
)
                    
                    await log_referral(bot, referrer_id, message.from_user)
                except: pass

# 🔥 ЕСЛИ ЭТО POST LINK - СПЕЦИАЛЬНОЕ ПРИВЕТСТВИЕ
            if is_post_link and post_config:
                # Сохраняем настройки в state
                await state.update_data(
                    broadcast_prompt=post_config.prompt,
                    broadcast_ratio=post_config.aspect_ratio,
                    broadcast_model=post_config.model_type,  # 👈 ДОБАВЬ ЭТУ СТРОКУ
                    from_broadcast=True
                )
                
                # Включаем режим генерации
                from app.handlers.generation import GenState
                await state.set_state(GenState.free_mode)
                
                word = get_banana_word(welcome_bonus)
                text = (
                    f"👋 Привет! Я вижу, ты пришел за этим образом! 🔥\n\n"
                    f"Я <b>Nano Banana Pro 🍌</b> — твой AI-фотошоп.\n\n"
                    f"🎁 Твои {welcome_bonus} приветственных {word} уже начислены.\n"
                    f"✨ Я уже настроил нужный промт.\n\n"
                    f"Ничего нажимать не надо — просто пришли мне свое фото, "
                    f"и я сделаю кадр как в посте! 👇"
                )
                
                await message.answer(text, parse_mode="HTML", reply_markup=get_main_kb())
                return
            # Обычное приветствие для новых юзеров
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            word = get_banana_word(welcome_bonus)
            text = (
                f"👋 Привет! Я <b>Nano Banana Pro 🍌</b> — твой AI-фотошоп.\n\n"
                f"🎁 <b>Тебе уже начислено {welcome_bonus} подарочных {word}</b>\n\n"
                f"<b>Я готов творить!</b>\n"
                f"Напиши, что создать, или пришли <b>от 1 до 4 фото</b>, которые нужно изменить или объединить\n\n"
                f"🤷‍♀️ <b>Не знаешь, что сгенерировать?👇</b>"
            )

                        # Создаем клавиатуру с кнопкой
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💃 Примерить образ", url="https://t.me/+qcYoFpW4yXRlZjVi")]
            ])

            
            try:
                if "AgAC" in WELCOME_PHOTO: 
                    await message.answer_photo(WELCOME_PHOTO, caption=text, parse_mode="HTML", reply_markup=keyboard,link_preview_options=types.LinkPreviewOptions(is_disabled=True)  # 👈 ДОБАВЬ ЭТО
)
                else: 
                    await message.answer(text, parse_mode="HTML", reply_markup=keyboard,link_preview_options=types.LinkPreviewOptions(is_disabled=True)  # 👈 ДОБАВЬ ЭТО
)
            except: 
                await message.answer(text, parse_mode="HTML", reply_markup=keyboard,link_preview_options=types.LinkPreviewOptions(is_disabled=True)  # 👈 ДОБАВЬ ЭТО
)
                
            # 🆕 ДОБАВЬ ЭТУ СТРОКУ!
            return
        # ЕСЛИ СТАРЫЙ ЮЗЕР
        else:

# 🔥 ЕСЛИ POST LINK - ПРИМЕНЯЕМ НАСТРОЙКИ И УВЕДОМЛЯЕМ
            if is_post_link and post_config:
                await state.update_data(
                    broadcast_prompt=post_config.prompt,
                    broadcast_ratio=post_config.aspect_ratio,
                    broadcast_model=post_config.model_type,  # 👈 ДОБАВЬ ЭТУ СТРОКУ
                    from_broadcast=True
                )
                
                from app.handlers.generation import GenState
                await state.set_state(GenState.free_mode)
                
                await message.answer(
                    "✨ <b>Промт из поста применен!</b>\n\n"
                    "📸 Присылай фото, чтобы сгенерировать 👇",
                    parse_mode="HTML",
                    reply_markup=get_main_kb()
                )
                return

            # Если перешел по рекламе - обновляем источник!
            if source and source != "ref_friend":
                if user.source != source:
                    user.source = source
                    await session.commit()

            bal = user.generations_balance
            word = get_banana_word(bal)
            text = (
            f"👋 *С возвращением!*\n"
            f"🍌 Твой баланс: *{bal} {word}*\n\n"
            f"*Я готов творить!*\n"
            f"Напиши, что создать, или пришли *от 1 до 4 фото*, которые нужно изменить или объединить\n\n"
            f"*Не знаешь, что создать?👇*"
)
            # Создаем inline-кнопку для старого юзера
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard_old = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💃 Примерить образ", url="https://t.me/+3ovTRpUPci85ODYy")]
            ])
            
            await message.answer(text, parse_mode="Markdown", reply_markup=keyboard_old)