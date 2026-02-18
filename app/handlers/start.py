from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton  # 👈 ДОБАВЬ
from app.services.admin_logger import log_new_user, log_referral
from app.database import async_session
from app.services.user_service import get_user, create_user, admin_change_balance, track_banana_transaction
from app import config
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import re
from sqlalchemy import select
from app.models import PostConfig, AdScenario

router = Router()

WELCOME_PHOTO = "AgACAgIAAxkBAAIGbWky1V4aiUImfckmTzqXjKcykdunAAJqC2sb4L2ZSWGkUXDH06FzAQADAgADeQADNgQ"

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

    # 🔥 ПРОВЕРКА БЛОКИРОВКИ
    async with async_session() as session:
        check_user = await get_user(session, user_id)
        if check_user and check_user.is_blocked:
            return
    
    # 1. ЛОВИМ ИСТОЧНИК
    referrer_id = None
    source = None
    args = command.args
    yandex_client_id = None
    ad_scenario_key = None

    if args:
        # ===== ФОРМАТ 1: scenario_clientid (РЕКЛАМНЫЕ СЦЕНАРИИ) =====
        if '_' in args and not args.startswith('post_') and not args.startswith('cid_'):
            
            
            parts = args.rsplit('_', 1)
            scenario_key = parts[0]
            client_id_part = parts[1] if len(parts) > 1 else None
            
            # Валидация ClientID (15-20 цифр)
            if client_id_part and re.match(r'^\d{15,20}$', client_id_part):
                yandex_client_id = client_id_part
                ad_scenario_key = scenario_key
                source = f"ad_{scenario_key}"
                args = None
        
        # ===== ФОРМАТ 2: source__cid_123456 =====
        elif '__cid_' in args:
            parts = args.split('__cid_')
            source_part = parts[0]
            cid_part = parts[1] if len(parts) > 1 else None
            
            if cid_part and re.match(r'^\d{15,20}$', cid_part):
                yandex_client_id = cid_part
            
            args = source_part
        
        # ===== ФОРМАТ 3: cid_123456 =====
        elif args.startswith("cid_"):
            potential_cid = args.replace("cid_", "")
            if re.match(r'^\d{15,20}$', potential_cid):
                yandex_client_id = potential_cid
                args = None

    # 🔥 ПРОВЕРЯЕМ НА POST_XX
    is_post_link = False
    post_config = None
    
    if args and args.startswith("post_"):
        is_post_link = True
        async with async_session() as session:
            result = await session.execute(
                select(PostConfig).where(PostConfig.config_id == args)
            )
            post_config = result.scalar_one_or_none()
        
        if not post_config:
            await message.answer(
                "⚠️ <b>Ссылка устарела или неверна.</b>\n\n"
                "Попробуйте найти свежую ссылку в нашем канале!",
                parse_mode="HTML"
            )
            return
        
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
            source = args

    async with async_session() as session:
        # 🔥 ИЩЕМ СЦЕНАРИЙ В ОСНОВНОЙ СЕССИИ
        ad_scenario = None
        if ad_scenario_key:
            result = await session.execute(
                select(AdScenario).where(
                    AdScenario.scenario_key == ad_scenario_key,
                    AdScenario.is_active == True
                )
            )
            ad_scenario = result.scalar_one_or_none()            
            if ad_scenario:
                ad_scenario.total_starts += 1
                await session.commit()  # 👈 ДОБАВЬ ЭТУ СТРОКУ!
        
        is_ad_scenario = ad_scenario is not None
        
        user = await get_user(session, user_id)
    
        
        # ЕСЛИ НОВЫЙ ЮЗЕР
        if not user:
            
            # Определяем source
            if is_ad_scenario and ad_scenario:
                user_source = f"ad_{ad_scenario.scenario_key}"
            elif is_post_link and post_config:
                user_source = f"post_{post_config.config_id}"
            else:
                user_source = source
            
            await create_user(
                session, 
                telegram_id=user_id, 
                username=message.from_user.username, 
                full_name=message.from_user.full_name, 
                referrer_id=referrer_id,
                source=user_source
            )
            
            user = await get_user(session, user_id)
            
            if user:
                await log_new_user(bot, message.from_user, deep_link=args)
            
            # Сохраняем ClientID если есть
            if yandex_client_id and user:
                user.yandex_client_id = yandex_client_id
                
                # 🔥 ОТПРАВЛЯЕМ ЦЕЛЬ BOT_START В МЕТРИКУ
                try:
                    from app.services.yandex_metrica import metrica_service
                    if metrica_service:
                        await metrica_service.send_bot_start_event(
                            client_id=yandex_client_id
                        )
                except Exception as e:
                    print(f"⚠️ Ошибка отправки BOT_START: {e}")
            
            # Сохраняем сценарий к пользователю
            if ad_scenario and user:
                user.active_scenario_id = ad_scenario.id
            
            await session.commit()
            
            # Начисляем бонусы
            await admin_change_balance(session, user_id, welcome_bonus)
            await track_banana_transaction(session, user_id, welcome_bonus, "welcome", "Welcome bonus")
            await session.commit()

            # 🔥 ЕСЛИ ЭТО РЕКЛАМНЫЙ СЦЕНАРИЙ - СПЕЦИАЛЬНОЕ ПРИВЕТСТВИЕ
            if is_ad_scenario and ad_scenario:                
                await state.update_data(
                    ad_scenario_id=ad_scenario.id,
                    ad_scenario_prompt=ad_scenario.prompt,
                    ad_scenario_ratio=ad_scenario.aspect_ratio,
                    ad_scenario_model=ad_scenario.model_type,
                    from_ad_scenario=True
                )
                
                from app.handlers.generation import GenState
                await state.set_state(GenState.free_mode)
                            
            # 🔥 КНОПКИ ДЛЯ РЕКЛАМНОГО СЦЕНАРИЯ
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💃 Выбрать образ", url="https://t.me/+qcYoFpW4yXRlZjVi")],  # 👈 Первая строка
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_scenario")]  # 👈 Вторая строка
                ])
                
                await message.answer(
                    ad_scenario.welcome_text, 
                    parse_mode="HTML", 
                    reply_markup=keyboard
                )
                
                return

            # 🔥 ЕСЛИ ЭТО POST LINK - СПЕЦИАЛЬНОЕ ПРИВЕТСТВИЕ
            if is_post_link and post_config:
                await state.update_data(
                    broadcast_prompt=post_config.prompt,
                    broadcast_ratio=post_config.aspect_ratio,
                    broadcast_model=post_config.model_type,
                    from_broadcast=True
                )
                
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
            word = get_banana_word(welcome_bonus)
            text = (
                f"👋 Привет! Я <b>Nano Banana Pro 🍌</b> — твой AI-фотошоп.\n\n"
                f"🎁 <b>Тебе уже начислено {welcome_bonus} подарочных {word}</b>\n\n"
                f"<b>Я готов творить!</b>\n"
                f"Напиши, что создать, или пришли <b>от 1 до 4 фото</b>, которые нужно изменить или объединить\n\n"
                f"🤷‍♀️ <b>Не знаешь, что сгенерировать?👇</b>"
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💃 Выбрать образ", url="https://t.me/+qcYoFpW4yXRlZjVi")]
            ])
            
            try:
                if "AgAC" in WELCOME_PHOTO: 
                    await message.answer_photo(
                        WELCOME_PHOTO, 
                        caption=text, 
                        parse_mode="HTML", 
                        reply_markup=keyboard,
                        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
                    )
                else: 
                    await message.answer(
                        text, 
                        parse_mode="HTML", 
                        reply_markup=keyboard,
                        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
                    )
            except: 
                await message.answer(
                    text, 
                    parse_mode="HTML", 
                    reply_markup=keyboard,
                    link_preview_options=types.LinkPreviewOptions(is_disabled=True)
                )
            await message.answer("☝️", reply_markup=get_main_kb())
            return
        
        # ЕСЛИ СТАРЫЙ ЮЗЕР
        else:            
            # Обновляем ClientID если пришел новый
            if user and yandex_client_id and user.yandex_client_id != yandex_client_id:
                user.yandex_client_id = yandex_client_id
            
            # Обновляем сценарий
            if ad_scenario:
                user.active_scenario_id = ad_scenario.id
            
            await session.commit()

            # 🔥 ЕСЛИ РЕКЛАМНЫЙ СЦЕНАРИЙ - ПРИМЕНЯЕМ НАСТРОЙКИ
            if is_ad_scenario and ad_scenario:
                await state.update_data(
                    ad_scenario_id=ad_scenario.id,
                    ad_scenario_prompt=ad_scenario.prompt,
                    ad_scenario_ratio=ad_scenario.aspect_ratio,
                    ad_scenario_model=ad_scenario.model_type,
                    from_ad_scenario=True
                )
                
                from app.handlers.generation import GenState
                await state.set_state(GenState.free_mode)
                
                await message.answer(
                    "✨ <b>Настройки из рекламы применены!</b>\n\n"
                    "📸 Присылай фото, чтобы сгенерировать 👇",
                    parse_mode="HTML",
                    reply_markup=get_main_kb()
                )
                return

            # 🔥 ЕСЛИ POST LINK - ПРИМЕНЯЕМ НАСТРОЙКИ
            if is_post_link and post_config:
                await state.update_data(
                    broadcast_prompt=post_config.prompt,
                    broadcast_ratio=post_config.aspect_ratio,
                    broadcast_model=post_config.model_type,
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

            # Если перешел по рекламе - обновляем источник
            if source and source != "ref_friend":
                if user and user.source != source:
                    user.source = source
                    await session.commit()

            bal = user.generations_balance
            word = get_banana_word(bal)
            text = (
                f"👋 *С возвращением!*\n"
                f"🍌 Твой баланс: *{bal} {word}*\n\n"
                f"*Я готов творить! 🎨*\n"
                f"Пришли *от 1 до 4 фото* с описанием или напиши, что сделать.\n\n"
                f"*Не знаешь, что создать? 👇*"
            )

            # Создаем inline-кнопку для старого юзера
            keyboard_old = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💃 Выбрать образ", url="https://t.me/+3ovTRpUPci85ODYy")]
            ])

            await message.answer(text, parse_mode="Markdown", reply_markup=keyboard_old)
            # 👆 ВСЁ! Больше ничего не нужно

@router.callback_query(F.data == "cancel_scenario")
async def callback_cancel_scenario(callback: CallbackQuery, state: FSMContext):
    """Отмена рекламного сценария"""
    await state.clear()
    
    await callback.message.edit_text(
        "❌ <b>Отменено</b>\n\n"
        "Возвращайся когда захочешь! 😊\n\n"
        "Используй кнопки ниже 👇",
        parse_mode="HTML"
    )
    await callback.answer()
