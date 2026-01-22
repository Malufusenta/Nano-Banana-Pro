from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Purchase, BananaTransaction, Broadcast, PostConfig
from datetime import datetime, timedelta

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
        func.count(Purchase.id).label('total_transactions')
    ).where(
        Purchase.status == 'succeeded',
        Purchase.created_at >= date_from,
        Purchase.created_at <= date_to
    )
    revenue_result = await session.execute(revenue_query)
    revenue_data = revenue_result.first()
    
    total_revenue = revenue_data.total_revenue or 0
    total_transactions = revenue_data.total_transactions or 0
    
    # Первые и повторные покупки
    first_purchases = await session.scalar(
        select(func.count(User.id)).where(
            User.first_purchase_at >= date_from,
            User.first_purchase_at <= date_to
        )
    ) or 0
    
    repeat_purchases = total_transactions - first_purchases
    
    # Средний чек
    avg_check = round(total_revenue / total_transactions, 2) if total_transactions > 0 else 0
    
    # ========== ВЫРУЧКА ПО ИСТОЧНИКАМ ==========
    
    source_revenue_query = select(
        Purchase.user_source,
        func.sum(Purchase.price).label('revenue'),
        func.count(Purchase.id).label('count')
    ).where(
        Purchase.status == 'succeeded',
        Purchase.completed_at >= date_from,
        Purchase.completed_at <= date_to
    ).group_by(Purchase.user_source).order_by(func.sum(Purchase.price).desc())
    
    source_revenue_result = await session.execute(source_revenue_query)
    revenue_by_source = {
        row.user_source or 'organic': {'revenue': row.revenue, 'count': row.count}
        for row in source_revenue_result
    }
    
    # ========== НОВЫЕ ПОЛЬЗОВАТЕЛИ ПО ИСТОЧНИКАМ ==========
    
    # Считаем сколько новых пользователей пришло с каждого источника
    users_by_source_query = select(
        User.source,
        func.count(User.id).label('count')
    ).where(
        User.created_at >= date_from,
        User.created_at <= date_to,
        User.source.isnot(None)
    ).group_by(User.source).order_by(func.count(User.id).desc())
    
    users_by_source_result = await session.execute(users_by_source_query)
    users_by_source = {
        row.source: row.count for row in users_by_source_result
    }
    
    # Считаем сколько уникальных покупателей с каждого источника
    buyers_by_source_query = select(
        Purchase.user_source,
        func.count(func.distinct(Purchase.user_id)).label('buyers')
    ).where(
        Purchase.status == 'succeeded',
        Purchase.completed_at >= date_from,
        Purchase.completed_at <= date_to
    ).group_by(Purchase.user_source)
    
    buyers_by_source_result = await session.execute(buyers_by_source_query)
    buyers_by_source = {
        row.user_source or 'organic': row.buyers for row in buyers_by_source_result
    }
    
    # ========== КОНВЕРСИЯ И СРЕДНИЙ ЧЕК ПО ИСТОЧНИКАМ ==========
    
    source_stats = {}
    for source in set(list(users_by_source.keys()) + list(revenue_by_source.keys())):
        total_users = users_by_source.get(source, 0)
        buyers = buyers_by_source.get(source, 0)
        revenue_info = revenue_by_source.get(source, {'revenue': 0, 'count': 0})
        
        # Конверсия
        conversion = (buyers / total_users * 100) if total_users > 0 else 0
        
        # Средний чек
        source_avg_check = (revenue_info['revenue'] / buyers) if buyers > 0 else 0

        
        source_stats[source] = {
            'total_users': total_users,
            'buyers': buyers,
            'conversion': conversion,
            'revenue': revenue_info['revenue'],
            'transactions': revenue_info['count'],
            'avg_check': source_avg_check  # ← ПЕРЕИМЕНОВАЛИ

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
            Purchase.completed_at <= date_to
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
    
    # Старички (повторная покупка в периоде, но не первая покупка в жизни)
    veteran_buyers_query = select(func.count(func.distinct(Purchase.user_id))).where(
        Purchase.status == 'succeeded',
        Purchase.completed_at >= date_from,
        Purchase.completed_at <= date_to,
        Purchase.user_id.in_(
            select(User.telegram_id).where(User.first_purchase_at < date_from)
        )
    )
    veteran_buyers = await session.scalar(veteran_buyers_query) or 0
    
# Заблокировали (всего за всё время, т.к. нет поля blocked_at)
    blocked = await session.scalar(
        select(func.count(User.id)).where(
            User.is_blocked == True
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
    
    # ========== ФОРМИРУЕМ РЕЗУЛЬТАТ ==========
    
    return {
        'revenue': {
            'total': total_revenue,
            'transactions': total_transactions,
            'first_purchases': first_purchases,
            'repeat_purchases': repeat_purchases,
            'avg_check': avg_check
        },
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
            'blocked': blocked
        }
    }

def format_report_message(data: dict, date_str: str) -> str:
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
    text += f"Выручка: {rev['total']:.0f} ₽\n"
    text += f"Транзакций всего: {rev['transactions']}\n"
    text += f"— Первых: {rev['first_purchases']}\n"
    text += f"— Повторных: {rev['repeat_purchases']}\n"
    text += f"Средний чек: {rev['avg_check']:.2f} ₽\n\n"
    
    # ИСТОЧНИКИ ТРАФИКА
    if source_stats:
        text += "📊 <b>ИСТОЧНИКИ ТРАФИКА</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        
        # 1. Новые пользователи
        text += "👥 <b>Новые пользователи:</b>\n"
        for source, stats in sorted(source_stats.items(), key=lambda x: x[1]['total_users'], reverse=True):
            if stats['total_users'] > 0:
                # Жирным выделяем количество
                text += f"   • {source}: <b>{stats['total_users']}</b> чел\n"
        
        # 2. Конверсия
        text += "\n💰 <b>Конверсия в покупку:</b>\n"
        sources_with_users = {s: stats for s, stats in source_stats.items() if stats['total_users'] > 0}
        for source, stats in sorted(sources_with_users.items(), key=lambda x: x[1]['conversion'], reverse=True):
            # Жирным выделяем процент
            text += f"   • {source}: <b>{stats['conversion']:.1f}%</b> ({stats['buyers']}/{stats['total_users']})\n"
        
        # 3. Выручка
        text += "\n💵 <b>Выручка по источникам:</b>\n"
        for source, stats in sorted(source_stats.items(), key=lambda x: x[1]['revenue'], reverse=True):
            if stats['revenue'] > 0:
                avg = stats['avg_check']
                # Жирным выделяем сумму выручки
                text += f"   • {source}: <b>{stats['revenue']:.0f} ₽</b> (ср.чек: {avg:.0f} ₽)\n"
        
        text += "\n"
    
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
    tariff_order = ["8 бананов", "44 банана", "140 бананов", "340 бананов", "832 банана", "Telegram Stars"]
    tariff_emojis = {
        "8 бананов": "🍌",
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
    text += f"Заблокировали: {users['blocked']} (всего за всё время)"
    
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
        1: 0, 2: 0, 3: 0, 4: 0, 5: 0,
        '6-10': 0, '11-20': 0, '21+': 0,
        'record': {'user_id': None, 'n': 0}
    }
    
    # Статистика по источникам: {'source_name': {'revenue': 0, 1: 0, 2: 0 ...}}
    sources_stats = {}

    for row in rows:
        n = row.purchase_num
        user_id = row.user_id
        price = row.price or 0
        source = row.user_source or "organic" # Если пусто, считаем органикой

        # --- Обновляем общую статистику ---
        global_stats['total'] += 1
        
        # Обновляем рекорд
        if n > global_stats['record']['n']:
            global_stats['record'] = {'user_id': user_id, 'n': n}

        # Категория глубины
        depth_key = None
        if n <= 5:
            global_stats[n] += 1
            depth_key = n
        elif 6 <= n <= 10:
            global_stats['6-10'] += 1
            depth_key = '6-10'
        elif 11 <= n <= 20:
            global_stats['11-20'] += 1
            depth_key = '11-20'
        else:
            global_stats['21+'] += 1
            depth_key = '21+'

        # --- Обновляем статистику источника ---
        if source not in sources_stats:
            sources_stats[source] = {
                'revenue': 0,
                1: 0, 2: 0, 3: 0, 4: 0, 5: 0,
                '6-10': 0, '11-20': 0, '21+': 0
            }
        
        sources_stats[source]['revenue'] += price
        sources_stats[source][depth_key] += 1

    return {
        'global': global_stats,
        'sources': sources_stats
    }


def format_payment_depth_message(data: dict, start_date: str, end_date: str) -> str:
    """
    Формирует отчет: Общая воронка + Детализация по источникам (Strict ТЗ Version)
    """
    g_stats = data['global']
    s_stats = data['sources']
    total = g_stats['total']

    if total == 0:
        return f"📊 ГЛУБИНА ПЛАТЕЖЕЙ ({start_date} - {end_date})\n\nДанных за этот период нет."

    def calc_percent(val, total_val):
        return f"{(val / total_val * 100):.1f}%" if total_val > 0 else "0%"

# === БЛОК 1: ОБЩАЯ СТАТИСТИКА ===
    text = f"📊 <b>ГЛУБИНА ПЛАТЕЖЕЙ</b> (Период: {start_date} - {end_date})\n"
    text += f"Всего транзакций: <b>{total}</b>\n\n"

    # Вывод общей статистики
    text += f"1️⃣ 1-я покупка: <b>{g_stats[1]} шт. ({calc_percent(g_stats[1], total)})</b>\n"
    text += f"2️⃣ 2-я покупка: <b>{g_stats[2]} шт. ({calc_percent(g_stats[2], total)})</b>\n"
    text += f"3️⃣ 3-я покупка: <b>{g_stats[3]} шт. ({calc_percent(g_stats[3], total)})</b>\n"
    text += f"4️⃣ 4-я покупка: <b>{g_stats[4]} шт. ({calc_percent(g_stats[4], total)})</b>\n"
    text += f"5️⃣ 5-я покупка: <b>{g_stats[5]} шт. ({calc_percent(g_stats[5], total)})</b>\n"
    
    text += f"🔄 6-10 покупок: <b>{g_stats['6-10']} шт. ({calc_percent(g_stats['6-10'], total)})</b>\n"
    text += "<b>— Постоянники.</b>\n"
    
    text += f"🐳 11-20 покупок: <b>{g_stats['11-20']} шт. ({calc_percent(g_stats['11-20'], total)})</b>\n"
    text += "<b>— Наши лояльные «Киты».</b>\n"
    
    text += f"👑 21+ покупок: <b>{g_stats['21+']} шт. ({calc_percent(g_stats['21+'], total)})</b>\n"
    text += "<b>— Супер-VIP / Коммерческий трафик.</b>\n\n"

    if g_stats['record']['user_id']:
        text += f"🏆 Рекорд периода: Юзер `{g_stats['record']['user_id']}` совершил свою <b>{g_stats['record']['n']}-ю</b> покупку.\n"

    # === БЛОК 2: КАЧЕСТВО ТРАФИКА (ПО ИСТОЧНИКАМ) ===
    if s_stats:
        text += "\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        text += "🎯 <b>КАЧЕСТВО ТРАФИКА (Детализация по источникам)</b>\n\n"

        # Сортируем источники по Выручке
        sorted_sources = sorted(s_stats.items(), key=lambda x: x[1]['revenue'], reverse=True)

        count_shown = 0
        for source, stat in sorted_sources:
            # Скрываем источники без покупок в этом периоде
            src_total = sum([stat[k] for k in [1,2,3,4,5,'6-10','11-20','21+']])
            if src_total == 0: continue

            # Лимит вывода (чтобы не упереться в лимит сообщения Телеграм)
            if count_shown >= 15:
                text += f"<i>... и еще {len(sorted_sources) - 15} источников скрыто</i>"
                break
            
            count_shown += 1

            # Заголовок источника
            text += f"📌 <b>Источник: {source}</b> (Выручка: {stat['revenue']:.0f} ₽)\n"
            
            # Строки выводим только если там есть значения, чтобы отчет был чистым
            if stat[1] > 0: text += f"   1️⃣ 1-я покупка: {stat[1]} шт.\n"
            if stat[2] > 0: text += f"   2️⃣ 2-я покупка: {stat[2]} шт.\n"
            if stat[3] > 0: text += f"   3️⃣ 3-я покупка: {stat[3]} шт.\n"
            if stat[4] > 0: text += f"   4️⃣ 4-я покупка: {stat[4]} шт.\n"
            if stat[5] > 0: text += f"   5️⃣ 5-я покупка: {stat[5]} шт.\n"
            
            if stat['6-10'] > 0:
                text += f"   🔄 6-10 покупок: {stat['6-10']} шт.\n"
            
            # Жирным выделяем важное
            if stat['11-20'] > 0:
                text += f"   🐳 <b>11-20 покупок: {stat['11-20']} шт. — Наши лояльные «Киты»</b>\n"
            
            if stat['21+'] > 0:
                text += f"   👑 <b>21+ покупок: {stat['21+']} шт. — Супер-VIP</b>\n"
            
            text += "\n"

    return text