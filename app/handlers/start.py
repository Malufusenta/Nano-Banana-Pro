from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from app.services.admin_logger import log_new_user, log_referral
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
    
    # 🕵️‍♂️ ОПРЕДЕЛЯЕМ ИСТОЧНИК (DeepLink)
    referrer_id = None
    source = None
    args = command.args # То, что написано после ?start=
    
    if args:
        # Вариант А: Это ЦИФРЫ -> значит ID друга (Рефералка)
        if args.isdigit():
            possible_ref = int(args)
            if possible_ref != user_id: # Нельзя пригласить самого себя
                referrer_id = possible_ref
                source = "ref_friend" 
        # Вариант Б: Это ТЕКСТ -> значит Рекламная метка (например: mts_ads)
        else:
            source = args # Сохраняем название кампании как источник

    async with async_session() as session:
        user = await get_user(session, user_id)
        
        # 🆕 СЦЕНАРИЙ: НОВЫЙ ПОЛЬЗОВАТЕЛЬ
        if not user:
            # 1. Создаем пользователя, записывая и РЕФЕРЕРА, и ИСТОЧНИК
            await create_user(
                session, 
                telegram_id=user_id, 
                username=message.from_user.username, 
                full_name=message.from_user.full_name, 
                referrer_id=referrer_id,
                source=source # ✅ Вот сюда пишется метка
            )

            # 2. Логируем админу (передаем исходный args, чтобы ты видел конкретный ID или название метки)
            await log_new_user(bot, message.from_user, deep_link=args)

            # 3. Начисляем бонус новичку (+2)
            welcome_bonus = 2
            await admin_change_balance(session, user_id, welcome_bonus)
            
            # 4. Начисляем бонус другу (+2), если он есть
            if referrer_id:
                try:
                    await admin_change_balance(session, referrer_id, 2)
                    await bot.send_message(referrer_id, "🎉 **Друг перешел по ссылке!**\n🍌 Тебе начислено: +2 банана", parse_mode="Markdown")
                    await log_referral(bot, referrer_id, message.from_user)
                except: pass

            # Текст приветствия (оставляем твой)
            word = get_banana_word(welcome_bonus)
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
                await message.answer(text, parse_mode="Markdown", reply_markup=get_main_kb())

        # 👴 СЦЕНАРИЙ: СТАРЫЙ ПОЛЬЗОВАТЕЛЬ
        else:
            bal = user.generations_balance
            if bal == 0:
                # 🛠 ИСПРАВЛЕНО: Убраны лишние звездочки (** -> *)
                text = (
                    f"👋 С возвращением!\n"
                    f"🍌 Твой баланс: *0 бананов*\n\n"
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