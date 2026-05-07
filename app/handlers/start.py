import asyncio
from aiogram import Router, types, F, Bot, html
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton  # 👈 ДОБАВЬ
from app.services.admin_logger import log_new_user, log_referral
from app.database import async_session
from app.services.user_service import get_user, admin_change_balance, track_banana_transaction
from app import config
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy.dialects.postgresql import insert as pg_insert
import re
from sqlalchemy import select
from app.models import PostConfig, AdScenario, User
from app.services.i18n import resolve_locale, t

router = Router()

WELCOME_PHOTO = "AgACAgIAAxkBAAIGbWky1V4aiUImfckmTzqXjKcykdunAAJqC2sb4L2ZSWGkUXDH06FzAQADAgADeQADNgQ"

def get_banana_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11: return "банан"
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20): return "банана"
    return "бананов"


def get_banana_word_by_locale(n: int, locale: str) -> str:
    if locale == "ru":
        return get_banana_word(n)
    if n == 1:
        return t("banana.one", locale)
    return t("banana.many", locale)

def _menu_labels(key: str) -> set[str]:
    return {t(key, "ru"), t(key, "en"), t(key, "es")}


def get_main_kb(locale: str = "ru"):
    kb = [
        [KeyboardButton(text=t("menu.create", locale))],
        [KeyboardButton(text=t("menu.proxy", locale))],
        [KeyboardButton(text=t("menu.buy", locale)), KeyboardButton(text=t("menu.profile", locale))],
        [KeyboardButton(text=t("menu.guide", locale)), KeyboardButton(text=t("menu.support", locale))]
    ]
    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        input_field_placeholder=t("menu.input_placeholder", locale),
    )

@router.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext, bot: Bot):
    
    await state.clear()
    user_id = message.from_user.id
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    from app.config import BONUS_AMOUNT; welcome_bonus = BONUS_AMOUNT

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
        # Убираем суффикс __cid без числа (Яндекс не подставил clientid)
        args = re.sub(r'__cid_?$', '', args) or None
        # Убираем префикс ad_ (нормализуем все рекламные источники)
        if args and args.startswith('ad_'):
            args = args[3:] or None

    if args:
        # ===== ФОРМАТ 2: source__cid_123456 (проверяем первым, т.к. тоже содержит '_') =====
        if '__cid_' in args:
            parts = args.split('__cid_')
            source_part = parts[0]
            cid_part = parts[1] if len(parts) > 1 else None

            if cid_part and re.match(r'^\d{15,20}$', cid_part):
                yandex_client_id = cid_part

            # Нормализуем ключ: "ad_yandex_rsya_3" → "yandex_rsya_3"
            clean_key = re.sub(r'^ad_', '', source_part)
            if clean_key:
                ad_scenario_key = clean_key
                source = clean_key

            args = None

        # ===== ФОРМАТ 1: scenario_clientid (РЕКЛАМНЫЕ СЦЕНАРИИ) =====
        elif '_' in args and not args.startswith('post_') and not args.startswith('cid_'):
            parts = args.rsplit('_', 1)
            scenario_key = parts[0]
            client_id_part = parts[1] if len(parts) > 1 else None

            # Валидация ClientID (15-20 цифр)
            if client_id_part and re.match(r'^\d{15,20}$', client_id_part):
                yandex_client_id = client_id_part
                ad_scenario_key = scenario_key
                source = scenario_key
                args = None

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
                t("start.postlink_invalid", locale),
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
                user_source = ad_scenario.scenario_key
            elif is_post_link and post_config:
                user_source = post_config.config_id
            else:
                user_source = source
            
            stmt = pg_insert(User).values(
                telegram_id=user_id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
                locale=locale,
                referrer_id=referrer_id,
                source=user_source,
                generations_balance=0,
                balance_free=0,
                balance_paid=0,
		preferred_model="nb2",
            ).on_conflict_do_nothing(index_elements=['telegram_id'])
            
            await session.execute(stmt)
            await session.commit()

            user = await get_user(session, user_id)
            
            if user:
                await log_new_user(bot, message.from_user, deep_link=source or args)
            
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
                    [InlineKeyboardButton(text=t("start.choose_look_button", locale), url="https://t.me/+qcYoFpW4yXRlZjVi")],  # 👈 Первая строка
                    [InlineKeyboardButton(text=t("common.cancel_button", locale), callback_data="cancel_scenario")]  # 👈 Вторая строка
                ])
                
                await message.answer(
                    ad_scenario.welcome_text, 
                    parse_mode="HTML", 
                    reply_markup=keyboard
                )
                
                return

            # 🔥 ЕСЛИ ЭТО POST LINK - СПЕЦИАЛЬНОЕ ПРИВЕТСТВИЕ
            if is_post_link and post_config:
                from app.handlers.generation import GenState

                pq = (post_config.param_question or "").strip()
                if pq:
                    await state.update_data(
                        param_main_prompt_template=post_config.prompt,
                        param_question_text=pq,
                        broadcast_ratio=post_config.aspect_ratio,
                        broadcast_model=post_config.model_type,
                        from_broadcast=True,
                        current_post_id=post_config.config_id,
                        pending_param_photo_file_id=None,
                    )
                    await state.set_state(GenState.waiting_for_prompt_text)
                    word = get_banana_word_by_locale(welcome_bonus, locale)
                    await message.answer(
                        t("start.postlink_with_question_intro", locale, bonus=welcome_bonus, suffix=word),
                        parse_mode="HTML",
                        reply_markup=get_main_kb(locale),
                    )
                    await asyncio.sleep(0.8)
                    safe_q = html.quote(pq)
                    await message.answer(
                        f"❓ <i>{safe_q}</i>\n\n{t('prompt.answer_here', locale)}",
                        parse_mode="HTML",
                    )
                    return

                await state.update_data(
                    broadcast_prompt=post_config.prompt,
                    broadcast_ratio=post_config.aspect_ratio,
                    broadcast_model=post_config.model_type,
                    from_broadcast=True,
                    current_post_id=post_config.config_id
                )
                await state.set_state(GenState.free_mode)

                word = get_banana_word_by_locale(welcome_bonus, locale)
                text = t("start.postlink_ready", locale, bonus=welcome_bonus, suffix=word)

                await message.answer(text, parse_mode="HTML", reply_markup=get_main_kb(locale))
                return
            
            # Обычное приветствие для новых юзеров
            word = get_banana_word_by_locale(welcome_bonus, locale)
            text = t("start.new_user", locale, bonus=welcome_bonus, suffix=word)

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("start.choose_look_button", locale), url="https://t.me/+qcYoFpW4yXRlZjVi")]
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
            await message.answer("☝️", reply_markup=get_main_kb(locale))
            return
        
        # ЕСЛИ СТАРЫЙ ЮЗЕР
        else:
            # Заполняем source только если ещё не был установлен (не перезаписываем атрибуцию)
            if user and not user.source and source:
                user.source = source

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
                    t("start.ad_settings_applied", locale),
                    parse_mode="HTML",
                    reply_markup=get_main_kb(locale)
                )
                return

            # 🔥 ЕСЛИ POST LINK - ПРИМЕНЯЕМ НАСТРОЙКИ
            if is_post_link and post_config:
                from app.handlers.generation import GenState, send_param_prompt_text_intro

                pq = (post_config.param_question or "").strip()
                if pq:
                    await state.update_data(
                        param_main_prompt_template=post_config.prompt,
                        param_question_text=pq,
                        broadcast_ratio=post_config.aspect_ratio,
                        broadcast_model=post_config.model_type,
                        from_broadcast=True,
                        current_post_id=post_config.config_id,
                        pending_param_photo_file_id=None,
                    )
                    await state.set_state(GenState.waiting_for_prompt_text)
                    await send_param_prompt_text_intro(
                        message.bot, message.chat.id, pq, locale=locale, reply_markup=get_main_kb(locale)
                    )
                    return

                await state.update_data(
                    broadcast_prompt=post_config.prompt,
                    broadcast_ratio=post_config.aspect_ratio,
                    broadcast_model=post_config.model_type,
                    from_broadcast=True,
                    current_post_id=post_config.config_id
                )
                await state.set_state(GenState.free_mode)

                await message.answer(
                    t("start.post_prompt_applied", locale),
                    parse_mode="HTML",
                    reply_markup=get_main_kb(locale)
                )
                return

            # Если перешел по рекламе - обновляем источник
            # if source and source != "ref_friend":
            #     if user and user.source != source:
            #         user.source = source
            #         await session.commit()

            bal = user.generations_balance
            word = get_banana_word_by_locale(bal, locale)
            text = (
                f"{t('start.returning_user', locale, balance=bal, suffix=word)}\n\n"
                f"{t('generation.start_creating_text', locale)}"
            )

            # Создаем inline-кнопку для старого юзера
            keyboard_old = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("start.choose_look_button", locale), url="https://t.me/+3ovTRpUPci85ODYy")]
            ])

            await message.answer(text, parse_mode="Markdown", reply_markup=keyboard_old)
            await message.answer("👆", reply_markup=get_main_kb(locale))
            # 👆 ВСЁ! Больше ничего не нужно

@router.callback_query(F.data == "cancel_scenario")
async def callback_cancel_scenario(callback: CallbackQuery, state: FSMContext):
    """Отмена рекламного сценария"""
    await state.clear()
    
    await callback.message.edit_text(
        t("common.cancelled", resolve_locale(callback.from_user.language_code if callback.from_user else None)),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(F.text.in_(_menu_labels("menu.proxy")))
async def proxy_handler(message: types.Message):
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=t("proxy.button", locale),
            url="tg://proxy?server=147.45.175.171&port=443&secret=ee9fae4791bf577d103c3730790ff0c325676f6f676c652e636f6d"
        )]
    ])
    await message.answer(
        t("proxy.text", locale),
        parse_mode="HTML",
        reply_markup=keyboard,
        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
    )

@router.message(Command("proxy"))
async def proxy_command_handler(message: types.Message):
    await proxy_handler(message)
