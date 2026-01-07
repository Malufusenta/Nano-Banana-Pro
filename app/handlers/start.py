from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
# Теперь этот импорт точно сработает, файлы на месте
from app.services.admin_logger import log_new_user, log_referral
from app.database import async_session
from app.services.user_service import get_user, create_user, admin_change_balance
from app import config

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
    
    # 1. ЛОВИМ ИСТОЧНИК
    referrer_id = None
    source = None
    args = command.args # Хвостик ссылки
    
    if args:
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
                source=source # <--- ВАЖНО
            )

            # Шлем лог админу
            await log_new_user(bot, message.from_user, deep_link=args)

            # Начисляем бонусы
            welcome_bonus = 2
            await admin_change_balance(session, user_id, welcome_bonus)
            
            if referrer_id:
                try:
                    await admin_change_balance(session, referrer_id, 2)
                    await bot.send_message(referrer_id, "🎉 **Друг перешел по ссылке!**\n🍌 +2 банана", parse_mode="Markdown")
                    await log_referral(bot, referrer_id, message.from_user)
                except: pass

            word = get_banana_word(welcome_bonus)
            text = (
                    f"👋 Привет! Я *Nano Banana Pro 🍌* — твой карманный AI-фотошоп.\n\n"
                    f"🎁 *Тебе уже начислено {welcome_bonus} подарочных {word}*\n\n"
                    f"🤷‍♀️ *Не знаешь, что сгенерировать?* [Хочу фото, как с обложки]({CHANNEL_LINK})\n"
            )
            
            try:
                if "AgAC" in WELCOME_PHOTO: 
                    await message.answer_photo(WELCOME_PHOTO, caption=text, parse_mode="Markdown", reply_markup=get_main_kb())
                else: 
                    await message.answer(text, parse_mode="Markdown", reply_markup=get_main_kb())
            except: 
                await message.answer(text, parse_mode="Markdown", reply_markup=get_main_kb())

        # ЕСЛИ СТАРЫЙ ЮЗЕР
        else:
            # Если перешел по рекламе - обновляем источник!
            if source and source != "ref_friend":
                if user.source != source:
                    user.source = source
                    await session.commit()

            bal = user.generations_balance
            word = get_banana_word(bal)
            text = f"👋 *С возвращением!*\n🍌 Твой баланс: *{bal} {word}*"
            await message.answer(text, parse_mode="Markdown", reply_markup=get_main_kb())