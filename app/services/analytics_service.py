from sqlalchemy import select, func, and_, or_, case
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Purchase, BananaTransaction, Broadcast, PostConfig
import calendar
from datetime import datetime, timedelta
from app.models import VideoGenerationTask, FixedExpense
from app import config
from app.services.currency import get_usd_rate


async def get_analytics_report(session: AsyncSession, date_from: datetime, date_to: datetime):
    """
    Собирает все метрики для отчёта за период
    
    Args:
        date_from: начало периода (включительно)
        date_to: конец периода (включительно)
    
    Returns:
        dict с метриками по разделам
    """
    
    # ========== ДЕНЬГИ ==========
    
    # Выручка и транзакции
    # Выручка и транзакции
    revenue_query = select(
        func.sum(Purchase.price).label('total_revenue'),
        func.sum(Purchase.income_amount).label('total_income_amount'),
        func.count(Purchase.id).label('total_transactions')
    ).where(
        Purchase.status == 'succeeded',
        Purchase.completed_at >= date_from,
        Purchase.completed_at <= date_to,
        or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))  # ← Исключаем Stars
    )
    revenue_result = await session.execute(revenue_query)
    revenue_data = revenue_result.first()
    
    total_revenue = revenue_data.total_revenue or 0
    total_income_amount = round((revenue_data.total_income_amount or 0) / 100, 2)
    total_transactions = revenue_data.total_transactions or 0
    
    # Первые покупки (ТОЛЬКО рубли, без Stars)
    first_purchases = await session.scalar(
        select(func.count(func.distinct(Purchase.user_id))).where(
            Purchase.status == 'succeeded',
            Purchase.completed_at >= date_from,
            Purchase.completed_at <= date_to,
            or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None)),
            Purchase.user_id.in_(
                select(User.telegram_id).where(User.first_purchase_at >= date_from, User.first_purchase_at <= date_to)
            )
        )
    ) or 0
    
    repeat_purchases = total_transactions - first_purchases

        # Выручка от Telegram Stars (отдельно)
    stars_revenue_query = select(
        func.sum(Purchase.price).label('stars_revenue'),
        func.count(Purchase.id).label('stars_count')
    ).where(
        Purchase.status == 'succeeded',
        Purchase.completed_at >= date_from,  # 👈 ПОМЕНЯЛИ
        Purchase.completed_at <= date_to,    # 👈 ПОМЕНЯЛИ
        Purchase.tariff_name == 'Telegram Stars'
    )
    stars_result = await session.execute(stars_revenue_query)
    stars_data = stars_result.first()

    stars_revenue = float(stars_data.stars_revenue or 0)
    stars_count = stars_data.stars_count or 0

    # Конвертация Stars в рубли: 1 звезда = $0.013, минус комиссия Telegram 30%
    from app.services.currency import get_usd_rate
    usd_rate = await get_usd_rate()
    stars_revenue_usd = round(stars_revenue * 0.013, 2)
    stars_revenue_rub = round(stars_revenue_usd * usd_rate, 2)
    stars_net_usd = stars_revenue_usd  # без комиссии
    stars_net_rub = stars_revenue_rub  # без комиссии

    # Рублевая выручка (revenue_query уже исключил Stars)
    rub_revenue = total_revenue
    
    # Средний чек
    # Средний чек (ТОЛЬКО по рублям, без учёта звёзд)
    rub_transactions = total_transactions - stars_count
    avg_check = round(rub_revenue / rub_transactions, 2) if rub_transactions > 0 else 0
    
    # ========== ВЫРУЧКА ПО ИСТОЧНИКАМ ==========
    source_revenue_query = select(
        Purchase.user_source,
        func.sum(Purchase.price).label('revenue'),
        func.count(Purchase.id).label('count')
    ).where(
        Purchase.status == 'succeeded',
        Purchase.completed_at >= date_from,
        Purchase.completed_at <= date_to,
        or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))  # ← Исключаем Stars
    ).group_by(Purchase.user_source).order_by(func.sum(Purchase.price).desc())
    
    source_revenue_result = await session.execute(source_revenue_query)
    revenue_by_source = {
        row.user_source or 'organic': {'revenue': row.revenue, 'count': row.count}
        for row in source_revenue_result
    }

# ========== ВЫРУЧКА ПО ИСТОЧНИКАМ (ЕДИНЫЙ ЗАПРОС) ==========
    
    # Подзапрос: кто сделал первую покупку В этом периоде
    first_time_buyers_subquery = select(User.telegram_id).where(
        User.first_purchase_at >= date_from,
        User.first_purchase_at <= date_to
    )
    
    # 🔥 ЕДИНЫЙ ЗАПРОС: всё считаем за раз
    unified_revenue_query = select(
        Purchase.user_source,
        # Общая выручка
        func.sum(Purchase.price).label('total_revenue'),
        # Количество транзакций
        func.count(Purchase.id).label('transactions'),
        # New revenue
        func.sum(
            case(
                (Purchase.user_id.in_(first_time_buyers_subquery), Purchase.price),
                else_=0
            )
        ).label('new_revenue'),
        # Old revenue
        func.sum(
            case(
                (~Purchase.user_id.in_(first_time_buyers_subquery), Purchase.price),
                else_=0
            )
        ).label('old_revenue')
    ).where(
        Purchase.status == 'succeeded',
        Purchase.completed_at >= date_from,
        Purchase.completed_at <= date_to,
        or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))  # ← Исключаем Stars
    ).group_by(Purchase.user_source).order_by(func.sum(Purchase.price).desc())
    
    unified_result = await session.execute(unified_revenue_query)
    
    # Формируем словарь с ПОЛНЫМИ данными
    revenue_by_source = {}
    for row in unified_result:
        source = row.user_source or 'organic'
        revenue_by_source[source] = {
            'revenue': row.total_revenue or 0,
            'count': row.transactions or 0,
            'new_revenue': row.new_revenue or 0,
            'old_revenue': row.old_revenue or 0
        }

    # ========== НОВЫЕ ПОЛЬЗОВАТЕЛИ ПО ИСТОЧНИКАМ ==========
    
    # Считаем сколько новых пользователей пришло с каждого источника
    # Убрали фильтр User.source.isnot(None), чтобы считалась и органика
    users_by_source_query = select(
        User.source,
        func.count(User.id).label('count')
    ).where(
        User.created_at >= date_from,
        User.created_at <= date_to
    ).group_by(User.source).order_by(func.count(User.id).desc())
    
    users_by_source_result = await session.execute(users_by_source_query)
    users_by_source = {
        row.source or 'organic': row.count for row in users_by_source_result
    }
    
# ========== КОНВЕРСИЯ: ГИБРИДНАЯ (⚡️ Day 0 vs 🐢 Delayed) ==========
    
    # Мы смотрим на тех, кто сделал ПЕРВУЮ покупку в выбранном периоде.
    # И делим их по дате регистрации.
    hybrid_conversion_query = select(
        User.source,
        # ⚡️ Fresh: Дата регистрации ВХОДИТ в выбранный период
        func.sum(
            case(
                (User.created_at >= date_from, 1),
                else_=0
            )
        ).label('fresh_buyers'),
        
        # 🐢 Delayed: Дата регистрации МЕНЬШЕ начала периода (пришли раньше)
        func.sum(
            case(
                (User.created_at < date_from, 1),
                else_=0
            )
        ).label('delayed_buyers')
    ).where(
        User.first_purchase_at >= date_from,
        User.first_purchase_at <= date_to
    ).group_by(User.source)
    
    hybrid_result = await session.execute(hybrid_conversion_query)
    
    # Собираем словарь: {'source': {'fresh': 5, 'delayed': 3}}
    conversion_stats = {}
    for row in hybrid_result:
        src = row.source or 'organic'
        conversion_stats[src] = {
            'fresh': row.fresh_buyers or 0,
            'delayed': row.delayed_buyers or 0
        }

    # ========== СБОРКА ИТОГОВОЙ СТАТИСТИКИ ПО ИСТОЧНИКАМ ==========
    
    source_stats = {}
    # Объединяем все источники из трех словарей (юзеры, деньги, конверсии)
    all_sources = set(list(users_by_source.keys()) + list(revenue_by_source.keys()) + list(conversion_stats.keys()))
    
    for source in all_sources:
        total_users = users_by_source.get(source, 0)
        
        # Данные по выручке (из предыдущего шага)
        revenue_info = revenue_by_source.get(source, {'revenue': 0, 'count': 0, 'new_revenue': 0, 'old_revenue': 0})
        
        # Данные по конверсии (новые)
        conv_data = conversion_stats.get(source, {'fresh': 0, 'delayed': 0})
        fresh_buyers = conv_data['fresh']
        delayed_buyers = conv_data['delayed']
        
        # 1. Честная конверсия (Только новички / Всего регистраций)
        if total_users > 0:
            conversion_percent = (fresh_buyers / total_users * 100)
        else:
            conversion_percent = 0.0
            
        # 2. Средний чек (Выручка / Количество транзакций)
        # Лучше делить на транзакции, т.к. покупателей теперь сложно посчитать одной цифрой
        if revenue_info['count'] > 0:
            source_avg_check = revenue_info['revenue'] / revenue_info['count']
        else:
            source_avg_check = 0

        source_stats[source] = {
            'total_users': total_users,
            'conversion_percent': conversion_percent,  # % честной конверсии
            'fresh_buyers': fresh_buyers,              # ⚡️ Числитель (Day 0)
            'delayed_buyers': delayed_buyers,          # 🐢 Дожимы
            
            'revenue': revenue_info['revenue'],
            'new_revenue': revenue_info.get('new_revenue', 0), # Из детализации выручки
            'old_revenue': revenue_info.get('old_revenue', 0), # Из детализации выручки
            
            'transactions': revenue_info['count'],
            'avg_check': source_avg_check
        }
    
    # ========== ОБОРОТ БАНАНОВ ==========
    
    # Потрачено
    spent_query = select(func.sum(BananaTransaction.amount)).where(
        BananaTransaction.transaction_type == 'spent',
        BananaTransaction.created_at >= date_from,
        BananaTransaction.created_at <= date_to
    )
    spent = abs(await session.scalar(spent_query) or 0)
    
    # Выдано бесплатно (разбивка)
    ref_query = select(func.sum(BananaTransaction.amount)).where(
        BananaTransaction.transaction_type == 'earned_ref',
        BananaTransaction.created_at >= date_from,
        BananaTransaction.created_at <= date_to
    )
    earned_ref = await session.scalar(ref_query) or 0
    
    sub_query = select(func.sum(BananaTransaction.amount)).where(
        BananaTransaction.transaction_type == 'earned_sub',
        BananaTransaction.created_at >= date_from,
        BananaTransaction.created_at <= date_to
    )
    earned_sub = await session.scalar(sub_query) or 0
    
    welcome_query = select(func.sum(BananaTransaction.amount)).where(
        BananaTransaction.transaction_type == 'welcome',
        BananaTransaction.created_at >= date_from,
        BananaTransaction.created_at <= date_to
    )
    earned_welcome = await session.scalar(welcome_query) or 0
    
    # Куплено за деньги
    purchased_query = select(func.sum(BananaTransaction.amount)).where(
        BananaTransaction.transaction_type == 'purchased',
        BananaTransaction.created_at >= date_from,
        BananaTransaction.created_at <= date_to
    )
    purchased = await session.scalar(purchased_query) or 0

    # ========== ТРАТЫ НА KIE.AI ==========
    kie_query = select(
        func.sum(BananaTransaction.kie_credits_cost).label('total_credits'),
        func.count(BananaTransaction.id).label('total_gens'),
    ).where(
        BananaTransaction.transaction_type == 'spent',
        BananaTransaction.created_at >= date_from,
        BananaTransaction.created_at <= date_to,
        BananaTransaction.kie_credits_cost.isnot(None)
    )
    kie_result = await session.execute(kie_query)
    kie_data = kie_result.first()

    kie_total_credits = kie_data.total_credits or 0
    kie_total_usd = round(kie_total_credits * 0.005, 4)

    # По моделям
    kie_by_model_query = select(
        BananaTransaction.model_type,
        func.sum(BananaTransaction.kie_credits_cost).label('credits'),
        func.count(BananaTransaction.id).label('gens')
    ).where(
        BananaTransaction.transaction_type == 'spent',
        BananaTransaction.created_at >= date_from,
        BananaTransaction.created_at <= date_to,
        BananaTransaction.kie_credits_cost.isnot(None)
    ).group_by(BananaTransaction.model_type)

    kie_by_model_result = await session.execute(kie_by_model_query)
    kie_by_model = {
        row.model_type: {'credits': row.credits, 'gens': row.gens, 'usd': round(row.credits * 0.005, 4)}
        for row in kie_by_model_result
    }
    
    # Добавляем видео кредиты
    
    video_kie_query = select(
        func.sum(VideoGenerationTask.kie_credits_cost).label('total_credits'),
        func.count(VideoGenerationTask.id).label('total_gens'),
    ).where(
        VideoGenerationTask.created_at >= date_from,
        VideoGenerationTask.created_at <= date_to,
        VideoGenerationTask.kie_credits_cost.isnot(None)
    )
    video_kie_result = await session.execute(video_kie_query)
    video_kie_data = video_kie_result.first()
    video_credits = video_kie_data.total_credits or 0
    video_gens = video_kie_data.total_gens or 0

    # Суммируем с фото
    kie_total_credits += video_credits
    kie_total_usd = round(kie_total_credits * 0.005, 4)
    if video_credits > 0:
        kie_by_model['video'] = {
            'credits': video_credits,
            'gens': video_gens,
            'usd': round(video_credits * 0.005, 4)
        }

    # ========== ПОКУПКИ ПО ТАРИФАМ ==========
    
    tariff_query = select(
        Purchase.tariff_name,
        func.count(Purchase.id).label('count')
    ).where(
        Purchase.status == 'succeeded',
        Purchase.completed_at >= date_from,
        Purchase.completed_at <= date_to
    ).group_by(Purchase.tariff_name)
    
    tariff_result = await session.execute(tariff_query)
    purchases_by_tariff = {row.tariff_name: row.count for row in tariff_result}
    
    # ========== ЛЮДИ ==========
    
    # Новые (Start)
    new_users = await session.scalar(
        select(func.count(User.id)).where(
            User.created_at >= date_from,
            User.created_at <= date_to
        )
    ) or 0
    
    cohort_subquery = select(User.telegram_id).where(
        User.created_at >= date_from,
        User.created_at <= date_to
    ).scalar_subquery()

    funnel_start = new_users

    funnel_gen = await session.scalar(
        select(func.count(func.distinct(BananaTransaction.user_id))).where(
            BananaTransaction.transaction_type == 'spent',
            BananaTransaction.user_id.in_(cohort_subquery)
        )
    ) or 0

    funnel_spent_2 = await session.scalar(
        select(func.count()).select_from(
            select(BananaTransaction.user_id)
            .where(
                BananaTransaction.transaction_type == 'spent',
                BananaTransaction.user_id.in_(cohort_subquery)
            )
            .group_by(BananaTransaction.user_id)
            .having(func.count(BananaTransaction.id) >= 2)
            .subquery()
        )
    ) or 0

    funnel_shop = await session.scalar(
        select(func.count(User.id)).where(
            User.visited_shop_at.isnot(None),
            User.telegram_id.in_(cohort_subquery)
        )
    ) or 0

    funnel_paid = await session.scalar(
        select(func.count(func.distinct(Purchase.user_id))).where(
            Purchase.status == 'succeeded',
            Purchase.user_id.in_(cohort_subquery),
            Purchase.user_id.in_(
                select(User.telegram_id).where(
                    User.first_purchase_at >= date_from,
                    User.first_purchase_at <= date_to
                )
            )
        )
    ) or 0

    # Активные (DAU) - юзеры с активностью (генерации)
    active_users = await session.scalar(
    select(func.count(func.distinct(BananaTransaction.user_id))).where(
        BananaTransaction.transaction_type == 'spent',
        BananaTransaction.created_at >= date_from,
        BananaTransaction.created_at <= date_to
    )
) or 0

# Если 0 (нет данных в BananaTransaction) - используем last_generation_at как fallback
    if active_users == 0:
        active_users = await session.scalar(
        select(func.count(User.id)).where(
            User.last_generation_at >= date_from,
            User.last_generation_at <= date_to
        )
    ) or 0
    # Купило всего (уникальные покупатели)
    total_buyers = await session.scalar(
        select(func.count(func.distinct(Purchase.user_id))).where(
            Purchase.status == 'succeeded',
            Purchase.completed_at >= date_from,
            Purchase.completed_at <= date_to,
            or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))  # ← ДОБАВЬ

        )
    ) or 0
    
    # Конверсия
    conversion_rate = round(total_buyers / new_users * 100, 2) if new_users > 0 else 0
    
    # Новички (первая покупка в периоде)
    newbie_buyers = first_purchases
    
    # Сразу купили vs Сначала копили
    bought_immediately = await session.scalar(
        select(func.count(User.id)).where(
            User.first_purchase_at >= date_from,
            User.first_purchase_at <= date_to,
            User.had_free_actions_before_purchase == False
        )
    ) or 0
    
    farmed_first = newbie_buyers - bought_immediately

    # CAC знаменатель: все из когорты (зарегистрировались в периоде), у кого была хотя бы одна оплата
    cac_buyers = await session.scalar(
        select(func.count(func.distinct(Purchase.user_id))).where(
            Purchase.status == 'succeeded',
            or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None)),
            Purchase.user_id.in_(
                select(User.telegram_id).where(
                    User.created_at >= date_from,
                    User.created_at <= date_to
                )
            )
        )
    ) or 0
    
    # Старички (купил в периоде, но первая покупка была до периода)
    veteran_buyers = await session.scalar(
        select(func.count(func.distinct(Purchase.user_id))).where(
            Purchase.status == 'succeeded',
            Purchase.completed_at >= date_from,
            Purchase.completed_at <= date_to,
            or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None)),
            Purchase.user_id.in_(
                select(User.telegram_id).where(
                    User.first_purchase_at < date_from
                )
            )
        )
    ) or 0
    
    blocked = await session.scalar(
        select(func.count(User.id)).where(
            User.blocked_at >= date_from,
            User.blocked_at <= date_to
        )
    ) or 0
    
    # ========== СТАТИСТИКА ПРОМПТОВ ==========
    
    # Получаем broadcasts за период
    broadcasts_query = select(
        Broadcast.id,
        Broadcast.message_text,
        func.date(Broadcast.created_at).label('date')
    ).where(
        Broadcast.created_at >= date_from,
        Broadcast.created_at <= date_to
    )
    broadcasts_result = await session.execute(broadcasts_query)
    broadcasts_by_date = {}
    for row in broadcasts_result:
        date_key = row.date.isoformat() if hasattr(row.date, 'isoformat') else str(row.date)
        broadcasts_by_date[date_key] = {
            'id': row.id,
            'name': row.message_text
        }
    
    # Получаем post_configs за период
    post_configs_query = select(
        PostConfig.id,
        PostConfig.prompt,
        PostConfig.clicks_count,
        func.date(PostConfig.created_at).label('date')
    ).where(
        PostConfig.created_at >= date_from,
        PostConfig.created_at <= date_to
    )
    post_configs_result = await session.execute(post_configs_query)
    post_configs_by_date = {}
    for row in post_configs_result:
        date_key = row.date.isoformat() if hasattr(row.date, 'isoformat') else str(row.date)
        if date_key not in post_configs_by_date:
            post_configs_by_date[date_key] = []
        post_configs_by_date[date_key].append({
            'id': row.id,
            'prompt': row.prompt,
            'clicks': row.clicks_count
        })
    
    # Объединяем по датам: одна дата = один промпт-кампания
    prompt_campaigns = []
    all_dates = set(broadcasts_by_date.keys()) | set(post_configs_by_date.keys())
    
    for date_key in all_dates:
        broadcast = broadcasts_by_date.get(date_key)
        post_configs = post_configs_by_date.get(date_key, [])
        
        # Название из broadcast, клики из post_configs
        campaign_name = broadcast['name'] if broadcast else None
        total_clicks = sum(pc['clicks'] for pc in post_configs)
        
        # Если есть название или есть клики - добавляем
        if campaign_name or total_clicks > 0:
            # Обрезаем название до первых 50 символов для читаемости
            display_name = campaign_name[:50] if campaign_name else f"Промпт {date_key}"
            prompt_campaigns.append({
                'name': display_name,
                'clicks': total_clicks,
                'date': date_key
            })
    
    # Сортируем по популярности
    prompt_campaigns.sort(key=lambda x: x['clicks'], reverse=True)

    # Яндекс Директ расходы
    direct_data = {'total': 0, 'campaigns': {}, 'error': None}
    if config.YANDEX_DIRECT_TOKEN:
        from app.services.yandex_direct import get_direct_spending
        direct_data = await get_direct_spending(
            config.YANDEX_DIRECT_TOKEN,
            date_from.date() if hasattr(date_from, 'date') else date_from,
            date_to.date() if hasattr(date_to, 'date') else date_to
        )
    
    # ========== ФОРМИРУЕМ РЕЗУЛЬТАТ ==========
    # Retention и LTV (для отчёта "За всё время")
    retention = round(total_transactions / total_buyers, 2) if total_buyers > 0 else 0.00
    ltv = round(avg_check * retention, 2)
    # Фиксированные расходы
    fixed_result = await session.execute(select(FixedExpense))
    fixed_expenses = fixed_result.scalars().all()
    fixed_total_month = sum(e.amount_rub for e in fixed_expenses)
    days_in_month = calendar.monthrange(datetime.now().year, datetime.now().month)[1]
    fixed_daily = float(round(fixed_total_month / days_in_month, 2))
    return {
    'revenue': {
        'total': total_revenue,
        'income_amount': total_income_amount,  # 👈 ДОБАВИТЬ
        'rub_revenue': rub_revenue,
        'stars_revenue': stars_revenue,
        'stars_revenue_usd': stars_revenue_usd,
        'stars_revenue_rub': stars_revenue_rub,
        'stars_net_usd': stars_net_usd,
        'stars_net_rub': stars_net_rub,
        'stars_count': stars_count,
        'transactions': total_transactions + stars_count,
        'first_purchases': first_purchases,
        'cac_buyers': cac_buyers,
        'repeat_purchases': repeat_purchases,
        'avg_check': avg_check,
        'retention': retention,   # 👈 НОВОЕ
        'ltv': ltv                # 👈 НОВОЕ
        
    },
    # ... остальное без изменений
        'revenue_by_source': revenue_by_source,
        'source_stats': source_stats,  # НОВОЕ!
        'prompt_campaigns': prompt_campaigns,  # ← ДОБАВЬ ЭТУ СТРОКУ
        'bananas': {
            'spent': spent,
            'earned_ref': earned_ref,
            'earned_sub': earned_sub,
            'earned_welcome': earned_welcome,
            'purchased': purchased
        },
        'purchases_by_tariff': purchases_by_tariff,
        'users': {
            'new': new_users,
            'active': active_users,
            'total_buyers': total_buyers,
            'conversion_rate': conversion_rate,
            'newbie_buyers': newbie_buyers,
            'bought_immediately': bought_immediately,
            'farmed_first': farmed_first,
            'veteran_buyers': veteran_buyers,
            'fresh_buyers_total': sum(v['fresh'] for v in conversion_stats.values()),
            'blocked': blocked
        },
        'funnel': {
            'start': funnel_start,
            'spent_1': funnel_gen,
            'spent_2': funnel_spent_2,
            'shop': funnel_shop,
            'paid': funnel_paid
        },
        'kie': {
            'total_credits': kie_total_credits,
            'total_usd': kie_total_usd,
            'by_model': kie_by_model
        },
        'fixed_expenses': {
            'total_month': fixed_total_month,
            'daily': fixed_daily,
            'items': [{'name': e.name, 'amount': e.amount_rub} for e in fixed_expenses]
        },

        'direct': direct_data,
    }


async def format_report_message(data: dict, date_str: str, is_all_time: bool = False) -> str:
    """
    Форматирует данные аналитики в красивое сообщение по ТЗ
    
    Args:
        data: результат get_analytics_report()
        date_str: строка с датой/периодом для заголовка
    
    Returns:
        отформатированное сообщение
    """
    rev = data['revenue']
    sources = data['revenue_by_source']
    source_stats = data.get('source_stats', {})  # НОВОЕ!
    prompt_campaigns = data.get('prompt_campaigns', [])  # ← ДОБАВЬ ЭТУ СТРОКУ
    bananas = data['bananas']
    tariffs = data['purchases_by_tariff']
    users = data['users']
    
    # Формируем текст
    text = f"📊 Отчет за {date_str}\n\n"
    
    # ДЕНЬГИ
    text += "💰 ДЕНЬГИ\n"
    text += f"Выручка (рубли): {rev['rub_revenue']:.2f} ₽\n"
    text += f"Чистая выручка: {rev['income_amount']:.2f} ₽\n"

    # ПОСЛЕ блока 💰 ДЕНЬГИ, добавить:
    kie = data.get('kie', {})
    if kie.get('total_credits', 0) > 0:
        photo_credits = sum(s['credits'] for k, s in kie.get('by_model', {}).items() if k != 'video')
        photo_gens = sum(s['gens'] for k, s in kie.get('by_model', {}).items() if k != 'video')
        video_stats = kie.get('by_model', {}).get('video', {})
        video_credits = video_stats.get('credits', 0)
        video_gens = video_stats.get('gens', 0)

        text += "🤖 РАСХОДЫ НА ГЕНЕРАЦИИ (kie.ai)\n"
        text += f"Всего: {kie['total_credits']} кред. (${kie['total_usd']})\n"
        if photo_credits > 0:
            text += f"📸 Фото: {photo_gens} ген. / {photo_credits} кред. (${round(photo_credits * 0.005, 2)})\n"
            for model, stats in kie.get('by_model', {}).items():
                if model != 'video':
                    text += f"  — {model}: {stats['gens']} ген. / {stats['credits']} кред.\n"
        if video_credits > 0:
            text += f"🎬 Видео: {video_gens} ген. / {video_credits} кред. (${round(video_credits * 0.005, 2)})\n"
        text += "\n"
    fixed = data.get('fixed_expenses', {})
    if fixed.get('daily', 0) > 0:
        text += "🏢 ФИКС. РАСХОДЫ\n"
        for item in fixed.get('items', []):
            text += f"• {item['name']}: {item['amount']} ₽/мес\n"
        text += f"📅 В день: {fixed['daily']} ₽\n\n"

    direct = data.get('direct', {})
    if direct.get('error'):
        text += f"📢 ЯНДЕКС ДИРЕКТ\n❌ Ошибка: {direct['error']}\n\n"
    elif direct.get('total', 0) > 0:
        text += f"📢 ЯНДЕКС ДИРЕКТ (с НДС 20%)\n"
        text += f"Итого: {direct['total']} ₽\n"
        for camp_name, camp_cost in direct.get('campaigns', {}).items():
            text += f"• {camp_name}: {camp_cost} ₽\n"
        text += "\n"

    income = rev.get('rub_revenue', 0)
    income_net = rev.get('income_amount', 0)
    yokassa_fee = round(income - income_net, 2)
    kie_usd = data.get('kie', {}).get('total_usd', 0)
    usd_rate = await get_usd_rate()
    kie_rub = round(kie_usd * usd_rate, 2)
    fixed_day = float(fixed.get('daily', 0) or 0)
    direct_total = direct.get('total', 0)
    stars_net_rub = rev.get('stars_net_rub', 0)
    net_profit = round(income_net + stars_net_rub - kie_rub - fixed_day - direct_total, 2)
    margin = round((net_profit / (income_net + stars_net_rub)) * 100, 1) if (income_net + stars_net_rub) > 0 else 0
    new_buyers = rev.get('cac_buyers', 0)
    text += "📈 ГЛАВНЫЕ МЕТРИКИ\n"
    text += f"Чистая прибыль: {net_profit} ₽\n"
    text += f"Маржинальность: {margin}%\n"
    if yokassa_fee > 0:
        text += f"Комиссия ЮКасса: -{yokassa_fee:.2f} ₽\n"
    if direct_total > 0 and new_buyers > 0:
        text += f"CAC: {round(direct_total / new_buyers, 2)} ₽\n"
    else:
        text += f"CAC: — (нет расходов в Директ за период)\n"
    text += "\n"

    # Показываем звёзды только если они есть
    if rev['stars_count'] > 0:
        text += f"Выручка (звёзды): {rev['stars_revenue']:.0f} ⭐️ ({rev['stars_count']} транз.)\n"

    text += f"Транзакций всего: {rev['transactions']}\n"
    text += f"— Первых: {rev['first_purchases']}\n"
    text += f"— Повторных: {rev['repeat_purchases']}\n"
    text += f"Средний чек: {rev['avg_check']:.2f} ₽\n\n"

        # 👇 НОВЫЙ БЛОК — только для "За всё время"
    if is_all_time:
        text += "📈 ЦЕННОСТЬ КЛИЕНТА (LTV)\n"
        text += f"Retention (покупок на юзера): {rev['retention']:.2f}\n"
        text += f"Выручка с одного клиента: {rev['ltv']:.2f} ₽\n\n"
    
    # ИСТОЧНИКИ ТРАФИКА
    if source_stats:
        text += "📊 <b>ИСТОЧНИКИ ТРАФИКА</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        
        # 1. Новые пользователи (Все источники)
        text += "👥 <b>Новые пользователи:</b>\n"
        # Сортируем и выводим ВСЕХ
        for source, stats in sorted(source_stats.items(), key=lambda x: x[1]['total_users'], reverse=True):
            if stats['total_users'] > 0:
                text += f"   • {source}: <b>{stats['total_users']}</b> чел\n"
        
# 2. Конверсия
        text += "\n💰 <b>Конверсия в покупку:</b>\n" # Вернули старое название
        
        # Вернули сортировку по ПРОЦЕНТУ (от большего к меньшему)
        sorted_conv = sorted(source_stats.items(), key=lambda x: x[1]['conversion_percent'], reverse=True)
        
        count_conv_shown = 0
        for source, stats in sorted_conv:
            fresh = stats['fresh_buyers']
            delayed = stats['delayed_buyers']
            regs = stats['total_users']
            percent = stats['conversion_percent']
            
            # Показываем, если есть хоть одна продажа (быстрая или отложенная)
            if (fresh + delayed) > 0:
                # Формат: • source: 5.0% (5/100) ⚡️ + 🐢 3 шт.
                text += (
                    f"   • {source}: <b>{percent:.1f}%</b> ({fresh}/{regs}) ⚡️ "
                    f"+ 🐢 <b>{delayed} шт.</b>\n"
                )
                count_conv_shown += 1
        
        if count_conv_shown == 0:
             text += "   <i>Нет первых покупок за этот период</i>\n"
        
# 3. Выручка (Все источники) - ДВУХЭТАЖНЫЙ ФОРМАТ
        text += "\n💵 <b>Выручка по источникам:</b>\n"
        for source, stats in sorted(source_stats.items(), key=lambda x: x[1]['revenue'], reverse=True):
            if stats['revenue'] > 0:
                total_rev = stats['revenue']
                avg_check = stats['avg_check']
                new_rev = stats.get('new_revenue', 0)
                old_rev = stats.get('old_revenue', 0)
                
                # Первая строка: источник, общая выручка, средний чек
                text += f"   • {source}: <b>{total_rev:.2f} ₽</b> (ср: {avg_check:.2f} ₽)\n"
                
                # Вторая строка: детализация New + Old
                text += f"     ↳ {new_rev:.2f} ₽ (New) + {old_rev:.2f} ₽ (Old)\n"
    
    # ОБОРОТ БАНАНОВ
    total_earned = bananas['earned_ref'] + bananas['earned_sub'] + bananas['earned_welcome']
    text += "🍌 ОБОРОТ БАНАНОВ\n"
    text += f"🔥 Потрачено: {bananas['spent']} шт.\n"
    text += f"🎁 Выдано бесплатно: {total_earned} шт.\n"
    text += f"— За рефералов: {bananas['earned_ref']}\n"
    text += f"— За подписки на канал: {bananas['earned_sub']}\n"
    text += f"— Приветственных: {bananas['earned_welcome']}\n"
    text += f"🛒 Куплено за деньги: {bananas['purchased']} шт.\n\n"
    
    # ПОКУПКИ ПО ТАРИФАМ
    text += "📦 ПОКУПКИ ПО ТАРИФАМ\n"
    # Порядок тарифов по ТЗ
    tariff_order = ["10 бананов", "44 банана", "140 бананов", "340 бананов", "832 банана", "Telegram Stars"]
    tariff_emojis = {
        "10 бананов": "🍌",
        "44 банана": "🍌",
        "140 бананов": "🔥",
        "340 бананов": "🍌",
        "832 банана": "🔥",
        "Telegram Stars": "⭐️"
    }
    
    for tariff in tariff_order:
        count = tariffs.get(tariff, 0)
        emoji = tariff_emojis.get(tariff, "🍌")
        text += f"{emoji} {tariff}: {count} шт.\n"
    text += "\n"
    
    # ЛЮДИ
    text += "👥 ЛЮДИ\n"
    text += f"Новых (Start): {users['new']}\n"
    text += f"Активных (DAU): {users['active']}\n"
    text += f"Купило всего: {users['total_buyers']} чел. (CR: {users['conversion_rate']:.1f}%)\n"
    text += f"— Новичков (Первая покупка): {users['newbie_buyers']}\n"
    text += f"— Старичков (Повторная покупка): {users['veteran_buyers']}\n"
    text += f"Заблокировали: {users['blocked']}"
    
    # ВОРОНКА
    funnel = data.get('funnel', {})
    if funnel.get('start', 0) > 0:
        start = funnel['start']
        text += "\n\n🔽 ВОРОНКА\n"
        text += f"👤 /start: {start} (100%)\n"
        text += f"🍌 1 генерация: {funnel['spent_1']} ({round(funnel['spent_1']/start*100, 1)}%)\n"
        text += f"🍌🍌 2+ генерации: {funnel['spent_2']} ({round(funnel['spent_2']/start*100, 1)}%)\n"
        text += f"💰 Зашёл в магазин: {funnel['shop']} ({round(funnel['shop']/start*100, 1)}%)\n"
        text += f"✅ Оплатил: {funnel['paid']} ({round(funnel['paid']/start*100, 1)}%)\n"

    return text


# ... (начало файла с импортами и функцией get_analytics_report не трогаем) ...

async def get_payment_depth_stats(session: AsyncSession, date_from: datetime, date_to: datetime) -> dict:
    """
    Считает глубину платежей (общую и по источникам трафика).
    """
    # 1. Подзапрос: Нумеруем покупки + тянем источник и цену
    p_alias = Purchase
    subquery = select(
        p_alias.user_id,
        p_alias.created_at,
        p_alias.price,       # 👈 Нужно для выручки
        p_alias.user_source, # 👈 Нужно для группировки
        func.row_number().over(
            partition_by=p_alias.user_id,
            order_by=p_alias.created_at
        ).label('purchase_num')
    ).where(p_alias.status == 'succeeded').subquery()

    # 2. Основной запрос: Фильтруем по дате
    query = select(
        subquery.c.purchase_num, 
        subquery.c.user_id,
        subquery.c.price,
        subquery.c.user_source
    ).where(
        subquery.c.created_at >= date_from,
        subquery.c.created_at <= date_to
    )

    result = await session.execute(query)
    rows = result.all()

    # 3. Структура данных
    # Общая статистика
    global_stats = {
        'total': 0,
        1: {'count': 0, 'prices': {}},
        2: {'count': 0, 'prices': {}},
        3: {'count': 0, 'prices': {}},
        4: {'count': 0, 'prices': {}},
        5: {'count': 0, 'prices': {}},
        '6-10': {'count': 0, 'prices': {}},
        '11-20': {'count': 0, 'prices': {}},
        '21+': {'count': 0, 'prices': {}},
        'record': {'user_id': None, 'n': 0}
    }

    # Статистика по источникам
    sources_stats = {}

    for row in rows:
        n = row.purchase_num
        user_id = row.user_id
        price = row.price or 0
        source = row.user_source or "organic"

        # --- Обновляем общую статистику ---
        global_stats['total'] += 1
        
        # Обновляем рекорд
        if n > global_stats['record']['n']:
            global_stats['record'] = {'user_id': user_id, 'n': n}

        # Категория глубины
        if n <= 5:
            depth_key = n
        elif 6 <= n <= 10:
            depth_key = '6-10'
        elif 11 <= n <= 20:
            depth_key = '11-20'
        else:
            depth_key = '21+'

        # Обновляем счетчик и цены в глобальной статистике
        global_stats[depth_key]['count'] += 1
        global_stats[depth_key]['prices'][price] = global_stats[depth_key]['prices'].get(price, 0) + 1

        # --- Обновляем статистику источника ---
        if source not in sources_stats:
            sources_stats[source] = {
                'revenue': 0,
                1: {'count': 0, 'prices': {}},
                2: {'count': 0, 'prices': {}},
                3: {'count': 0, 'prices': {}},
                4: {'count': 0, 'prices': {}},
                5: {'count': 0, 'prices': {}},
                '6-10': {'count': 0, 'prices': {}},
                '11-20': {'count': 0, 'prices': {}},
                '21+': {'count': 0, 'prices': {}}
            }
        
        sources_stats[source]['revenue'] += price
        sources_stats[source][depth_key]['count'] += 1
        sources_stats[source][depth_key]['prices'][price] = sources_stats[source][depth_key]['prices'].get(price, 0) + 1

    return {
        'global': global_stats,
        'sources': sources_stats
    }

def format_price_breakdown(prices: dict) -> str:
    """
    Форматирует разбивку по ценам: (15×79₽, 5×299₽)
    
    Args:
        prices: словарь {цена: количество}
    
    Returns:
        строка вида "(15×79₽, 5×299₽)" или пустая строка
    """
    if not prices:
        return ""
    
    # Сортируем по частоте (от большего к меньшему)
    sorted_prices = sorted(prices.items(), key=lambda x: x[1], reverse=True)
    
    # Форматируем: "15×79₽, 5×299₽"
    parts = [f"{count}×{int(price)}₽" for price, count in sorted_prices]
    
    return f" ({', '.join(parts)})"

def format_payment_depth_message(data: dict, start_date: str, end_date: str) -> tuple[str, str]:
    """
    Формирует отчет: Общая воронка + Детализация по источникам
    
    Returns:
        (text_general, text_sources) - две части отчета
    """
    g_stats = data['global']
    s_stats = data['sources']
    total = g_stats['total']

    if total == 0:
        return f"📊 ГЛУБИНА ПЛАТЕЖЕЙ ({start_date} - {end_date})\n\nДанных за этот период нет.", ""

    def calc_percent(val, total_val):
        return f"{(val / total_val * 100):.1f}%" if total_val > 0 else "0%"

    # === БЛОК 1: ЗАГОЛОВОК ===
    if start_date == end_date:
        text_header = f"📊 <b>ГЛУБИНА ПЛАТЕЖЕЙ</b> (Период: {start_date})\n"
    else:
        text_header = f"📊 <b>ГЛУБИНА ПЛАТЕЖЕЙ</b> (Период: {start_date} - {end_date})\n"
    text_header += f"Всего транзакций: <b>{total}</b>\n\n"

    # === БЛОК 2: ОБЩАЯ СТАТИСТИКА ===
    text_global = ""
    text_global += f"1️⃣ 1-я покупка: <b>{g_stats[1]['count']} шт.{format_price_breakdown(g_stats[1]['prices'])} ({calc_percent(g_stats[1]['count'], total)})</b>\n"
    text_global += f"2️⃣ 2-я покупка: <b>{g_stats[2]['count']} шт.{format_price_breakdown(g_stats[2]['prices'])} ({calc_percent(g_stats[2]['count'], total)})</b>\n"
    text_global += f"3️⃣ 3-я покупка: <b>{g_stats[3]['count']} шт.{format_price_breakdown(g_stats[3]['prices'])} ({calc_percent(g_stats[3]['count'], total)})</b>\n"
    text_global += f"4️⃣ 4-я покупка: <b>{g_stats[4]['count']} шт.{format_price_breakdown(g_stats[4]['prices'])} ({calc_percent(g_stats[4]['count'], total)})</b>\n"
    text_global += f"5️⃣ 5-я покупка: <b>{g_stats[5]['count']} шт.{format_price_breakdown(g_stats[5]['prices'])} ({calc_percent(g_stats[5]['count'], total)})</b>\n"
    
    text_global += f"🔄 6-10 покупок: <b>{g_stats['6-10']['count']} шт.{format_price_breakdown(g_stats['6-10']['prices'])} ({calc_percent(g_stats['6-10']['count'], total)})</b>\n"
    text_global += "<b>— Постоянники.</b>\n"
    
    text_global += f"🐳 11-20 покупок: <b>{g_stats['11-20']['count']} шт.{format_price_breakdown(g_stats['11-20']['prices'])} ({calc_percent(g_stats['11-20']['count'], total)})</b>\n"
    text_global += "<b>— Наши лояльные «Киты».</b>\n"
    
    text_global += f"👑 21+ покупок: <b>{g_stats['21+']['count']} шт.{format_price_breakdown(g_stats['21+']['prices'])} ({calc_percent(g_stats['21+']['count'], total)})</b>\n"
    text_global += "<b>— Супер-VIP / Коммерческий трафик.</b>\n\n"

    # === БЛОК 3: РЕКОРД ===
    text_record = ""
    if g_stats['record']['user_id']:
        text_record = f"🏆 Рекорд периода: Юзер `{g_stats['record']['user_id']}` совершил свою <b>{g_stats['record']['n']}-ю</b> покупку.\n"

    # === БЛОК 4: КАЧЕСТВО ТРАФИКА (ПО ИСТОЧНИКАМ) ===
    text_sources_block = ""
    if s_stats:
        text_sources_block += "\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        text_sources_block += "🎯 <b>КАЧЕСТВО ТРАФИКА (Детализация по источникам)</b>\n\n"

        # Сортируем источники по Выручке
        sorted_sources = sorted(s_stats.items(), key=lambda x: x[1]['revenue'], reverse=True)

        count_shown = 0
        for source, stat in sorted_sources:
            # Скрываем источники без покупок в этом периоде
            src_total = sum([stat[k]['count'] for k in [1,2,3,4,5,'6-10','11-20','21+']])
            if src_total == 0: continue

            # Лимит вывода (урезан до 10 для экономии места)
            if count_shown >= 10:
                text_sources_block += f"<i>... и еще {len(sorted_sources) - 10} источников скрыто</i>\n"
                break
            
            count_shown += 1

            # Заголовок источника
            text_sources_block += f"📌 <b>Источник: {source}</b> (Выручка: {stat['revenue']:.2f} ₽)\n"
            
            # Строки выводим только если там есть значения
            if stat[1]['count'] > 0:
                text_sources_block += f"   1️⃣ 1-я покупка: {stat[1]['count']} шт.{format_price_breakdown(stat[1]['prices'])}\n"
            if stat[2]['count'] > 0:
                text_sources_block += f"   2️⃣ 2-я покупка: {stat[2]['count']} шт.{format_price_breakdown(stat[2]['prices'])}\n"
            if stat[3]['count'] > 0:
                text_sources_block += f"   3️⃣ 3-я покупка: {stat[3]['count']} шт.{format_price_breakdown(stat[3]['prices'])}\n"
            if stat[4]['count'] > 0:
                text_sources_block += f"   4️⃣ 4-я покупка: {stat[4]['count']} шт.{format_price_breakdown(stat[4]['prices'])}\n"
            if stat[5]['count'] > 0:
                text_sources_block += f"   5️⃣ 5-я покупка: {stat[5]['count']} шт.{format_price_breakdown(stat[5]['prices'])}\n"
            
            if stat['6-10']['count'] > 0:
                text_sources_block += f"   🔄 6-10 покупок: {stat['6-10']['count']} шт.{format_price_breakdown(stat['6-10']['prices'])}\n"
            
            # Жирным выделяем важное
            if stat['11-20']['count'] > 0:
                text_sources_block += f"   🐳 <b>11-20 покупок: {stat['11-20']['count']} шт.{format_price_breakdown(stat['11-20']['prices'])} — Наши лояльные «Киты»</b>\n"
            
            if stat['21+']['count'] > 0:
                text_sources_block += f"   👑 <b>21+ покупок: {stat['21+']['count']} шт.{format_price_breakdown(stat['21+']['prices'])} — Супер-VIP</b>\n"
            
            text_sources_block += "\n"

    # Разделяем на 2 части
    text_general = text_header + text_global + text_record
    
    return text_general, text_sources_block


async def get_campaign_stats(session: AsyncSession, date_from: datetime, date_to: datetime) -> list[dict]:
    """
    Считает статистику по рекламным кампаниям (когортный метод).
    Правила:
    - Транзакция попадает ЛИБО в новички ЛИБО в старички — без дублей
    - Кампания показывается если есть хоть что-то: запуски, новички или выручка старичков
    - Отдельная строка "Органика / Рефералы" для бесплатного трафика
    """
    from app.models import CampaignMapping
    import json

    mappings_result = await session.execute(select(CampaignMapping))
    mappings = mappings_result.scalars().all()

    result = []

    for mapping in mappings:
        utm_sources = json.loads(mapping.utm_sources)

        cohort_subquery = select(User.telegram_id).where(
            User.created_at >= date_from,
            User.created_at <= date_to,
            User.source.in_(utm_sources)
        ).scalar_subquery()

        veterans_subquery = select(User.telegram_id).where(
            User.source.in_(utm_sources),
            User.created_at < date_from
        ).scalar_subquery()

        cohort_count = await session.scalar(
            select(func.count(User.id)).where(
                User.created_at >= date_from,
                User.created_at <= date_to,
                User.source.in_(utm_sources)
            )
        ) or 0

        fast_buyers = await session.scalar(
            select(func.count(User.id)).where(
                User.telegram_id.in_(cohort_subquery),
                User.first_purchase_at.isnot(None),
                func.date(User.first_purchase_at) == func.date(User.created_at)
            )
        ) or 0

        slow_buyers = await session.scalar(
            select(func.count(User.id)).where(
                User.telegram_id.in_(cohort_subquery),
                User.first_purchase_at.isnot(None),
                func.date(User.first_purchase_at) > func.date(User.created_at)
            )
        ) or 0

        # Выручка новичков — Day-0: оплатил в день регистрации (по дате оплаты)
        new_revenue = await session.scalar(
            select(func.sum(Purchase.price))
            .select_from(Purchase)
            .join(User, User.telegram_id == Purchase.user_id)
            .where(
                Purchase.status == 'succeeded',
                Purchase.user_id.in_(cohort_subquery),
                func.date(Purchase.completed_at) == func.date(User.created_at),
                or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))
            )
        ) or 0

        # Выручка старичков — покупки В периоде от пользователей зарегавшихся ДО периода
        # notin_(cohort_subquery) — защита от дублей
        old_revenue = await session.scalar(
            select(func.sum(Purchase.price)).where(
                Purchase.status == 'succeeded',
                Purchase.completed_at >= date_from,
                Purchase.completed_at <= date_to,
                Purchase.user_id.in_(veterans_subquery),
                Purchase.user_id.notin_(cohort_subquery),
                or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))
            )
        ) or 0

        # Количество уникальных старичков-покупателей за период
        old_buyers = await session.scalar(
            select(func.count(func.distinct(Purchase.user_id))).where(
                Purchase.status == 'succeeded',
                Purchase.completed_at >= date_from,
                Purchase.completed_at <= date_to,
                Purchase.user_id.in_(veterans_subquery),
                Purchase.user_id.notin_(cohort_subquery),
                or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))
            )
        ) or 0

        if cohort_count == 0 and new_revenue == 0 and old_revenue == 0:
            continue

        result.append({
            'campaign': mapping.yandex_campaign_name,
            'starts': cohort_count,
            'fast_buyers': fast_buyers,
            'slow_buyers': slow_buyers,
            'total_buyers': fast_buyers + slow_buyers,
            'new_revenue': new_revenue,
            'old_revenue': old_revenue,
            'old_buyers': old_buyers,
        })

    # --- Строка "Органика / Рефералы" ---
    organic_cohort_subquery = select(User.telegram_id).where(
        User.created_at >= date_from,
        User.created_at <= date_to,
        or_(User.source.in_(['organic', 'ref_friend']), User.source.is_(None))
    ).scalar_subquery()

    organic_veterans_subquery = select(User.telegram_id).where(
        User.created_at < date_from,
        or_(User.source.in_(['organic', 'ref_friend']), User.source.is_(None))
    ).scalar_subquery()

    organic_count = await session.scalar(
        select(func.count(User.id)).where(
            User.created_at >= date_from,
            User.created_at <= date_to,
            or_(User.source.in_(['organic', 'ref_friend']), User.source.is_(None))
        )
    ) or 0

    organic_fast = await session.scalar(
        select(func.count(User.id)).where(
            User.telegram_id.in_(organic_cohort_subquery),
            User.first_purchase_at.isnot(None),
            func.date(User.first_purchase_at) == func.date(User.created_at)
        )
    ) or 0

    organic_slow = await session.scalar(
        select(func.count(User.id)).where(
            User.telegram_id.in_(organic_cohort_subquery),
            User.first_purchase_at.isnot(None),
            func.date(User.first_purchase_at) > func.date(User.created_at)
        )
    ) or 0

    organic_new_revenue = await session.scalar(
        select(func.sum(Purchase.price))
        .select_from(Purchase)
        .join(User, User.telegram_id == Purchase.user_id)
        .where(
            Purchase.status == 'succeeded',
            Purchase.user_id.in_(organic_cohort_subquery),
            func.date(Purchase.completed_at) == func.date(User.created_at),
            or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))
        )
    ) or 0

    organic_old_revenue = await session.scalar(
        select(func.sum(Purchase.price)).where(
            Purchase.status == 'succeeded',
            Purchase.completed_at >= date_from,
            Purchase.completed_at <= date_to,
            Purchase.user_id.in_(organic_veterans_subquery),
            Purchase.user_id.notin_(organic_cohort_subquery),
            or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))
        )
    ) or 0

    organic_old_buyers = await session.scalar(
        select(func.count(func.distinct(Purchase.user_id))).where(
            Purchase.status == 'succeeded',
            Purchase.completed_at >= date_from,
            Purchase.completed_at <= date_to,
            Purchase.user_id.in_(organic_veterans_subquery),
            Purchase.user_id.notin_(organic_cohort_subquery),
            or_(Purchase.tariff_name != 'Telegram Stars', Purchase.tariff_name.is_(None))
        )
    ) or 0

    if organic_count > 0 or organic_new_revenue > 0 or organic_old_revenue > 0:
        result.append({
            'campaign': '🌱 Органика / Рефералы',
            'starts': organic_count,
            'fast_buyers': organic_fast,
            'slow_buyers': organic_slow,
            'total_buyers': organic_fast + organic_slow,
            'new_revenue': organic_new_revenue,
            'old_revenue': organic_old_revenue,
            'old_buyers': organic_old_buyers,
        })

    return result