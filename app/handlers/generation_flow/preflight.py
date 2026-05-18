"""
Preflight check - предварительная проверка перед генерацией
"""
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import html

from app.database import async_session
from app.services.user_service import (
    get_user_model_preference,
    set_user_model_preference,
    has_user_purchased,
    get_user,
)
from app.services.i18n import resolve_locale, t
from app.services.generation import calc_cost
from app.handlers.payment import get_banana_label
from app.utils.image_utils import normalize_image_urls
from app.keyboards.generation import get_preflight_kb, get_ratio_kb

# Импортируем GenState из родительского модуля
from app.handlers.generation_states import GenState
from app.services.generation.model_description import get_model_description

router = Router()


# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================

def _preflight_locale(user: types.User | None) -> str:
    return resolve_locale(user.language_code if user else None)


def _preflight_prompt_snippet(prompt: str, max_len: int = 35) -> str:
    raw = (prompt or "")[:max_len]
    return html.quote(raw) + "..."


def compose_preflight_message_html(
    locale: str,
    *,
    prompt_raw: str,
    cost: int,
    model: str,
    has_photo: bool,
    is_edit_mode: bool,
    is_broadcast: bool,
    use_settings_header: bool,
) -> str:
    if is_broadcast:
        return (
            f"{t('generation.preflight.header_params', locale)}\n\n"
            f"{t('generation.preflight.broadcast_cta', locale)}"
        )
    snippet = _preflight_prompt_snippet(prompt_raw)
    banana_suffix = get_banana_label(locale, cost)
    prompt_line = t("generation.preflight.prompt_line", locale, snippet=snippet)
    cost_line = t("generation.preflight.cost_line", locale, cost=cost, suffix=banana_suffix)
    footer = t("generation.preflight.footer", locale)
    model_desc = get_model_description(model, locale)
    header = (
        t("generation.preflight.header_settings", locale)
        if use_settings_header
        else t("generation.preflight.header_params", locale)
    )
    parts = [header, prompt_line, cost_line]
    if model_desc and not is_edit_mode:
        parts.append(model_desc)
    if (not has_photo) or is_edit_mode:
        parts.append(t("generation.preflight.warning", locale))
    parts.append(footer)
    return "\n\n".join(parts)


def compose_preflight_scenario_ready_html(locale: str) -> str:
    return (
        f"{t('generation.preflight.header_params', locale)}\n\n"
        f"{t('generation.preflight.scenario_ready', locale)}\n\n"
        f"{t('generation.preflight.tap_launch', locale)}"
    )


async def get_smart_alert_message(
    session, user_id: int, balance: int, cost: int, locale: str
) -> tuple[str, InlineKeyboardBuilder]:
    """
    Возвращает умное сообщение и клавиатуру в зависимости от сценария
    
    Returns:
        (text, keyboard_builder)
    """
    has_purchases = await has_user_purchased(session, user_id)
    
    builder = InlineKeyboardBuilder()
    
    # 🔹 СЦЕНАРИЙ В: Не хватает чуть-чуть (Баланс > 0, но < Цены)
    if balance > 0 and balance < cost:
        text = t(
            "generation.smart.need_more",
            locale,
            cost=cost,
            balance=balance,
        )
        builder.button(text=t("menu.buy", locale), callback_data="goto_shop")
        builder.adjust(1)
        return text, builder
    
    # 🔹 СЦЕНАРИЙ Б: Опытный (Баланс 0, покупки были)
    if balance == 0 and has_purchases:
        text = t("generation.smart.empty_buy", locale)
        builder.button(text=t("menu.buy", locale), callback_data="goto_shop")
        builder.adjust(1)
        return text, builder
    
    # 🔹 СЦЕНАРИЙ А: Новичок (Баланс 0, покупок не было)
    text = t("generation.smart.empty_newbie", locale)
    builder.button(text=t("menu.buy", locale), callback_data="goto_shop")
    builder.button(text=t("menu.free", locale), callback_data="goto_free")
    builder.adjust(1)
    return text, builder


# =====================================================================
# ГЛАВНАЯ ФУНКЦИЯ PREFLIGHT
# =====================================================================

async def start_preflight_check(
    message: types.Message,
    state: FSMContext,
    prompt: str,
    image_urls=None,
    is_edit_mode=False,
    initial_ratio=None
):
    user_id = message.from_user.id

    # 🔥 ПРОВЕРЯЕМ РЕКЛАМНЫЙ СЦЕНАРИЙ
    data = await state.get_data()

    from_ad_scenario = data.get("from_ad_scenario", False)
    
    # Если пришли из рекламного сценария - используем его настройки
    if from_ad_scenario:
        scenario_prompt = data.get("ad_scenario_prompt")
        scenario_model = data.get("ad_scenario_model", "standard")
        scenario_ratio = data.get("ad_scenario_ratio", "1:1")
        
        # Объединяем промт пользователя с промтом сценария
        combined_prompt = f"{scenario_prompt}, {prompt}" if prompt else scenario_prompt
        
        # Очищаем флаг
        await state.update_data(from_ad_scenario=False)
        
        # Нормализуем URL
        normalized_urls = normalize_image_urls(image_urls)
        
        await state.update_data(
            pf_prompt=combined_prompt,
            pf_image_urls=normalized_urls,
            pf_model=scenario_model,
            pf_ratio=scenario_ratio,
            pf_quality="hd" if scenario_model == "nb2" else "2k"
        )

        locale = _preflight_locale(message.from_user)
        text = compose_preflight_scenario_ready_html(locale)

        await message.answer(
            text,
            reply_markup=get_preflight_kb(scenario_model, scenario_ratio, "2k", locale),
            parse_mode="HTML",
        )
        return
    
    # 🔥 ОБЫЧНАЯ ЛОГИКА
    force_pro = data.get("force_pro_mode", False)
    from_broadcast = data.get("from_broadcast", False)
    
    async with async_session() as session:
        pref_model = "pro" if force_pro else await get_user_model_preference(session, user_id)
        user_obj = await get_user(session, user_id)
        is_new_user = user_obj is None or not user_obj.first_generation_done
        no_standard_model = from_broadcast or from_ad_scenario or is_new_user

        if no_standard_model and pref_model == "standard":
            pref_model = "nb2"

        saved_ratio = initial_ratio or getattr(user_obj, "last_image_ratio", "1:1") or "1:1"
    
    normalized_urls = normalize_image_urls(image_urls)
    
    await state.update_data(
        pf_prompt=prompt, 
        pf_image_urls=normalized_urls,
        pf_model=pref_model, 
        pf_ratio=saved_ratio, 
        pf_quality="hd" if pref_model == "nb2" else "2k",
        pf_is_edit_mode=is_edit_mode,
        no_standard_model=no_standard_model,
    )
    await state.set_state(GenState.preflight_check)
    
    quality = "2k"  # дефолтное качество при инициализации
    cost = calc_cost(pref_model, quality)
    has_photo = normalized_urls is not None and len(normalized_urls) > 0
    locale = _preflight_locale(message.from_user)
    if not has_photo or is_edit_mode:
        cost = calc_cost(pref_model, "hd")
        text = compose_preflight_message_html(
            locale,
            prompt_raw=prompt or "",
            cost=cost,
            model=pref_model,
            has_photo=has_photo,
            is_edit_mode=is_edit_mode,
            is_broadcast=False,
            use_settings_header=True,
        )
    else:
        cost = calc_cost(pref_model, "hd")
        text = compose_preflight_message_html(
            locale,
            prompt_raw=prompt or "",
            cost=cost,
            model=pref_model,
            has_photo=has_photo,
            is_edit_mode=is_edit_mode,
            is_broadcast=False,
            use_settings_header=False,
        )

    await message.answer(
        text,
        reply_markup=get_preflight_kb(pref_model, saved_ratio, "hd", locale),
        parse_mode="HTML",
    )


# =====================================================================
# КОЛБЕКИ PREFLIGHT
# =====================================================================

@router.callback_query(GenState.preflight_check, F.data == "pf_toggle_model")
async def cb_pf_toggle_model(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_model = data.get("pf_model", "standard")
    
    if data.get("no_standard_model"):
        new_model = "pro" if current_model == "nb2" else "nb2"
    else:
        if current_model == "standard":
            new_model = "nb2"
        elif current_model == "nb2":
            new_model = "pro"
        else:
            new_model = "standard"
    
    await state.update_data(pf_model=new_model)
    
    async with async_session() as session: 
        await set_user_model_preference(session, callback.from_user.id, new_model, manual=True)
    
    ratio = data.get("pf_ratio", "1:1")
    quality = data.get("pf_quality", "hd")
    
    # При переключении на nb2 сбрасываем качество на HD
    if new_model == "nb2":
        quality = "hd"
        await state.update_data(pf_quality="hd")
    
    cost = calc_cost(new_model, quality)

    is_broadcast = data.get("is_broadcast_gen", False)
    has_photo = bool(data.get("pf_image_urls"))
    is_edit_mode = data.get("pf_is_edit_mode", False)
    locale = _preflight_locale(callback.from_user)

    if is_broadcast:
        text = compose_preflight_message_html(
            locale,
            prompt_raw="",
            cost=0,
            model=new_model,
            has_photo=True,
            is_edit_mode=False,
            is_broadcast=True,
            use_settings_header=False,
        )
    else:
        prompt_raw = data.get("pf_prompt", "") or ""
        cost = calc_cost(new_model, quality)
        text = compose_preflight_message_html(
            locale,
            prompt_raw=prompt_raw,
            cost=cost,
            model=new_model,
            has_photo=has_photo,
            is_edit_mode=is_edit_mode,
            is_broadcast=False,
            use_settings_header=False,
        )

    await callback.message.edit_text(
        text,
        reply_markup=get_preflight_kb(new_model, ratio, quality, locale),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(GenState.preflight_check, F.data == "pf_toggle_quality")
async def cb_pf_toggle_quality(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_q = data.get("pf_quality", "2k")
    
    # ЦИКЛ: HD -> 2K -> 4K -> HD
    if current_q == "hd":
        new_q = "2k"
    elif current_q == "2k":
        new_q = "4k"
    else:
        new_q = "hd"
        
    await state.update_data(pf_quality=new_q)
    
    model = data.get("pf_model", "standard")
    ratio = data.get("pf_ratio", "1:1")

    locale = _preflight_locale(callback.from_user)
    prompt_raw = data.get("pf_prompt", "") or ""
    cost = calc_cost(model, new_q)
    has_photo = bool(data.get("pf_image_urls"))
    is_edit_mode = data.get("pf_is_edit_mode", False)
    text = compose_preflight_message_html(
        locale,
        prompt_raw=prompt_raw,
        cost=cost,
        model=model,
        has_photo=has_photo,
        is_edit_mode=is_edit_mode,
        is_broadcast=False,
        use_settings_header=False,
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_preflight_kb(model, ratio, new_q, locale),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(GenState.preflight_check, F.data == "pf_select_ratio")
async def cb_pf_select_ratio(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GenState.selecting_ratio)
    data = await state.get_data()
    model_type = data.get("pf_model", "standard")
    locale = _preflight_locale(callback.from_user)
    await callback.message.edit_text(
        t("generation.preflight.pick_ratio", locale),
        reply_markup=get_ratio_kb(model_type, locale),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(GenState.selecting_ratio, F.data == "pf_back")
async def cb_pf_ratio_back(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GenState.preflight_check)
    data = await state.get_data()
    model = data.get("pf_model")
    quality = data.get("pf_quality", "hd")
    cost = calc_cost(model, quality)

    # 🔥 ПРОВЕРЯЕМ ФЛАГ BROADCAST 🔥
    is_broadcast = data.get("is_broadcast_gen", False)
    locale = _preflight_locale(callback.from_user)

    if is_broadcast:
        text = compose_preflight_message_html(
            locale,
            prompt_raw="",
            cost=0,
            model=model,
            has_photo=True,
            is_edit_mode=False,
            is_broadcast=True,
            use_settings_header=False,
        )
    else:
        prompt_raw = data.get("pf_prompt", "") or ""
        cost = calc_cost(data.get("pf_model"), data.get("pf_quality"))
        has_photo = bool(data.get("pf_image_urls"))
        is_edit_mode = data.get("pf_is_edit_mode", False)
        text = compose_preflight_message_html(
            locale,
            prompt_raw=prompt_raw,
            cost=cost,
            model=data.get("pf_model", "standard"),
            has_photo=has_photo,
            is_edit_mode=is_edit_mode,
            is_broadcast=False,
            use_settings_header=False,
        )

    await callback.message.edit_text(
        text,
        reply_markup=get_preflight_kb(
            data.get("pf_model"),
            data.get("pf_ratio"),
            data.get("pf_quality"),
            locale,
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(GenState.selecting_ratio, F.data.startswith("set_ratio_"))
async def cb_pf_set_ratio(callback: types.CallbackQuery, state: FSMContext):
    new_ratio = callback.data.split("_")[2]
    await state.update_data(pf_ratio=new_ratio)
    
    # Сохраняем в БД
    async with async_session() as session:
        user_obj = await get_user(session, callback.from_user.id)
        if user_obj:
            user_obj.last_image_ratio = new_ratio
            await session.commit()
    
    await cb_pf_ratio_back(callback, state)


@router.callback_query(GenState.preflight_check, F.data == "pf_start")
async def cb_pf_start(callback: types.CallbackQuery, state: FSMContext):
    # Импортируем процесс генерации из родительского модуля
    from app.handlers.generation import process_generation
    
    data = await state.get_data()
    
    prompt = data.get("pf_prompt")
    image_urls = data.get("pf_image_urls")
    model_type = data.get("pf_model")
    ratio = data.get("pf_ratio")
    quality = data.get("pf_quality")
    
    cost = calc_cost(model_type, quality)
    
    use_pro = (model_type == "pro")
    use_nb2 = (model_type == "nb2")
    
    # Логика разрешения
    resolution = "1K"
    if use_pro or use_nb2:
        if quality == "4k":
            resolution = "4K"
        elif quality == "2k":
            resolution = "2K"
    
    await callback.answer(
        t("generation.preflight.starting", _preflight_locale(callback.from_user)),
        show_alert=False
    )
    
    await process_generation(
        callback.message,
        callback.from_user.id,
        prompt,
        image_urls,
        aspect_ratio=ratio,
        cost=cost,
        use_pro_model=use_pro,
        use_nb2_model=use_nb2,
        resolution=resolution,
        is_blend_mode=data.get("is_blend_mode", False),
        post_id=data.get("current_post_id"),
        locale=_preflight_locale(callback.from_user),
    )

    from_retry_flow = data.get("force_pro_mode", False)
    if from_retry_flow:
        await state.update_data(force_pro_mode=False)
        
        from app.services.admin_logger import log_order_from_retry
        await log_order_from_retry(
            callback.bot,
            callback.from_user.id,
            cost,
            model_type
        )
