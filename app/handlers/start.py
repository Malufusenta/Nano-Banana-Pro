from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from app.services.admin_logger import log_new_user
from app.database import async_session
from app.services.user_service import get_user, create_user, admin_change_balance
from app import config

router = Router()

# 👇 Твои настройки
WELCOME_PHOTO = "AgACAgIAAxkBAAIGbWky1V4aiUImfckmTzqXjKcykdunAAJqC2sb4L2ZSWGkUXDH06FzAQADAgADeQADNgQ"
CHANNEL_LINK = "https://t.me/nanobanan_promt"

def get_banana_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11: return "банан"
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20): return "банана"
    return "бананов"

# 👇 МЕНЮ
def get_main_kb():
    # 👇 ВСТАВЬ ЭТО ВНУТРЬ get_main_kb
    kb = [
        # Ряд 1: Самая главная
        [KeyboardButton(text="✨ Начать творить")],
        
        # Ряд 2: Коммерция и Личное
        [KeyboardButton(text="🍌 Купить бананы"), KeyboardButton(text="👤 Профиль")],
        
        # Ряд 3: Помощь и Обучение
        [KeyboardButton(text="📚 Гайд"), KeyboardButton(text="💬 Поддержка")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, input_field_placeholder="Пиши сюда ")

@router.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id
    
    # 1. ПРОВЕРЯЕМ РЕФЕРАЛЬНУЮ ССЫЛКУ
    referrer_id = None
    args = command.args
    if args and args.isdigit():
        possible_ref = int(args)
        if possible_ref != user_id:
            referrer_id = possible_ref

    async with async_session() as session:
        user = await get_user(session, user_id)
        
        # 🆕 СЦЕНАРИЙ: НОВЫЙ ПОЛЬЗОВАТЕЛЬ
        if not user:
            await create_user(session, telegram_id=user_id, username=message.from_user.username, full_name=message.from_user.full_name, referrer_id=referrer_id)

            # 👇 ДОБАВИТЬ ЛОГГЕР
            # args - это deeplink параметр (например рефка)
            await log_new_user(bot, message.from_user, deep_link=args)

            welcome_bonus = 2
            await admin_change_balance(session, user_id, welcome_bonus)
            
            # Бонус другу
            if referrer_id:
                try:
                    await admin_change_balance(session, referrer_id, 2)
                    await bot.send_message(referrer_id, "🎉 **Друг перешел по ссылке!**\n🍌 Тебе начислено: +2 банана", parse_mode="Markdown")
                except: pass

            word = get_banana_word(welcome_bonus)
            # 👇 Твой текст (без изменений, Markdown)
            text = (
                    f"👋 Привет! Я *Nano Banana Pro 🍌* — твой карманный AI-фотошоп.\n\n"
                    f"🎁 *Тебе уже начислено {welcome_bonus} подарочных {word}*\n"
                    f"💡 Идеи и промпты смотри тут: [Наш Канал]({CHANNEL_LINK})\n\n"
                    f"*Я готов творить!*\n"
                    f"Напиши, что создать, или пришли *от 1 до 4 фото*, которые нужно изменить или объединить 👇"
            )
            
            try:
                if "AgAC" in WELCOME_PHOTO: 
                    await message.answer_photo(WELCOME_PHOTO, caption=text, parse_mode="Markdown", reply_markup=get_main_kb())
                else: 
                    await message.answer(text, parse_mode="Markdown", reply_markup=get_main_kb())
            except Exception as e:
                print(f"Ошибка фото: {e}")
                # Если фото не грузится, шлем текст
                await message.answer(text, parse_mode="Markdown", reply_markup=get_main_kb())

        # 👴 СЦЕНАРИЙ: СТАРЫЙ ПОЛЬЗОВАТЕЛЬ
        else:
            bal = user.generations_balance
            if bal == 0:
                # 🛠 ИСПРАВЛЕНО: Убраны лишние звездочки (** -> *)
                text = (
                    f"👋 С возвращением!\n"
                    f"🍌 Твой баланс: *0 бананов*\n\n"
                    f"👇 Пополни запас кнопкой *[Заработать🍌]*"
                )
            else:
                word = get_banana_word(bal)
                # 🛠 ИСПРАВЛЕНО: Убраны лишние звездочки (** -> *)
                text = (
                    f"👋 *С возвращением!*\n"
                    f"🍌 Твой баланс: *{bal} {word}*\n\n"
                    f"*Я готов творить!*\n"
                    f"Напиши, что создать, или пришли *от 1 до 4 фото*, которые нужно изменить или объединить 👇"
                )
            
            await message.answer(text, parse_mode="Markdown", reply_markup=get_main_kb())