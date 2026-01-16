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
        avg_check = (revenue_info['revenue'] / buyers) if buyers > 0 else 0
        
        source_stats[source] = {
            'total_users': total_users,
            'buyers': buyers,
            'conversion': conversion,
            'revenue': revenue_info['revenue'],
            'transactions': revenue_info['count'],
            'avg_check': avg_check
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
    
    # ИСТОЧНИКИ ТРАФИКА (НОВЫЙ БЛОК!)
    if source_stats:
        text += "📊 ИСТОЧНИКИ ТРАФИКА\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        
        # Топ-5 по новым пользователям
        text += "👥 Новые пользователи:\n"
        for source, stats in sorted(source_stats.items(), key=lambda x: x[1]['total_users'], reverse=True)[:5]:
            if stats['total_users'] > 0:
                text += f"   • {source}: {stats['total_users']} чел\n"
        
        # Топ-5 по конверсии
        text += "\n💰 Конверсия в покупку:\n"
        sources_with_users = {s: stats for s, stats in source_stats.items() if stats['total_users'] > 0}
        for source, stats in sorted(sources_with_users.items(), key=lambda x: x[1]['conversion'], reverse=True)[:5]:
            text += f"   • {source}: {stats['conversion']:.1f}% ({stats['buyers']}/{stats['total_users']})\n"
        
        # Топ-5 по выручке
        text += "\n💵 Выручка по источникам:\n"
        for source, stats in sorted(source_stats.items(), key=lambda x: x[1]['revenue'], reverse=True)[:5]:
            if stats['revenue'] > 0:
                avg = stats['avg_check']
                text += f"   • {source}: {stats['revenue']:.0f} ₽ (ср.чек: {avg:.0f} ₽)\n"
        
        text += "\n"
    
    # ПОПУЛЯРНЫЕ ПРОМПТЫ (НОВЫЙ БЛОК!)
    if prompt_campaigns:
        text += "🎨 ПОПУЛЯРНЫЕ ПРОМПТЫ\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        
        # Топ-10 промптов
        total_prompt_clicks = sum(p['clicks'] for p in prompt_campaigns)
        for i, prompt in enumerate(prompt_campaigns[:10], 1):
            if prompt['clicks'] > 0:
                percentage = (prompt['clicks'] / total_prompt_clicks * 100) if total_prompt_clicks > 0 else 0
                text += f"{i}. {prompt['name']} — {prompt['clicks']} ген. ({percentage:.1f}%)\n"
        
        # Итоговая статистика
        text += f"\n📊 Всего уникальных промптов: {len(prompt_campaigns)}\n"
        text += f"🔥 Всего генераций: {total_prompt_clicks}\n\n"
    
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