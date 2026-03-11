"""
Разовый скрипт: восстанавливает income_amount из API ЮКассы
для всех purchases где income_amount IS NULL
"""
import asyncio
import aiohttp
from sqlalchemy import select, update
from app.database import async_session
from app.models import Purchase
from app.config import YOO_SHOP_ID, YOO_SECRET_KEY

async def fix_income_amount():
    async with async_session() as session:
        # Берем все purchases где income_amount IS NULL и есть payment_id
        result = await session.execute(
            select(Purchase.id, Purchase.payment_id).where(
                Purchase.income_amount == None,
                Purchase.payment_id != None,
                Purchase.status == 'succeeded'
            )
        )
        purchases = result.all()
        print(f"Найдено {len(purchases)} записей без income_amount")

        auth = aiohttp.BasicAuth(YOO_SHOP_ID, YOO_SECRET_KEY)
        updated = 0
        errors = 0

        async with aiohttp.ClientSession(auth=auth) as http:
            for purchase_id, payment_id in purchases:
                try:
                    async with http.get(
                        f"https://api.yookassa.ru/v3/payments/{payment_id}"
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            income_value = data.get('income_amount', {}).get('value')
                            if income_value:
                                income_kopecks = int(float(income_value) * 100)
                                await session.execute(
                                    update(Purchase).where(Purchase.id == purchase_id).values(
                                        income_amount=income_kopecks
                                    )
                                )
                                updated += 1
                                if updated % 50 == 0:
                                    await session.commit()
                                    print(f"Обновлено: {updated}/{len(purchases)}")
                        else:
                            errors += 1
                    await asyncio.sleep(0.05)  # 20 req/sec лимит ЮКассы
                except Exception as e:
                    print(f"Ошибка {payment_id}: {e}")
                    errors += 1

        await session.commit()
        print(f"✅ Готово! Обновлено: {updated}, ошибок: {errors}")

if __name__ == "__main__":
    asyncio.run(fix_income_amount())
