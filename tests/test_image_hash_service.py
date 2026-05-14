import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import ImageHash, User
from app.services.image_hash_service import (
    apply_duplicate_penalty,
    cleanup_expired_hashes,
    find_recent_duplicate_hash,
    should_enforce_image_hash_guard,
    should_run_image_hash_check,
    store_image_hashes,
)


class ImageHashServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def test_paid_user_bypasses_guard(self) -> None:
        user = User(telegram_id=101, balance_free=2, balance_paid=0, generations_balance=2)
        self.assertFalse(should_enforce_image_hash_guard(user, has_purchases=True))

    def test_text_only_request_skips_guard(self) -> None:
        user = User(telegram_id=102, balance_free=2, balance_paid=0, generations_balance=2)
        self.assertFalse(should_run_image_hash_check([], user, has_purchases=False))

    def test_duplicate_penalty_zeroes_only_free_balance(self) -> None:
        user = User(telegram_id=103, balance_free=2, balance_paid=5, generations_balance=7)
        apply_duplicate_penalty(user)
        self.assertEqual(user.balance_free, 0)
        self.assertEqual(user.balance_paid, 5)
        self.assertEqual(user.generations_balance, 5)

    async def test_duplicate_lookup_ignores_same_user_and_expired_records(self) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self.Session() as session:
            session.add_all(
                [
                    ImageHash(hash="deadbeefdeadbeef", user_id=1, created_at=now - timedelta(minutes=10)),
                    ImageHash(hash="deadbeefdeadbeef", user_id=2, created_at=now - timedelta(hours=25)),
                    ImageHash(hash="deadbeefdeadbeef", user_id=3, created_at=now - timedelta(minutes=5)),
                ]
            )
            await session.commit()

            duplicate = await find_recent_duplicate_hash(
                session,
                hash_values=["deadbeefdeadbeef"],
                user_id=1,
                now=now,
            )

        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate.user_id, 3)

    async def test_store_hashes_and_cleanup_expired_rows(self) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self.Session() as session:
            await store_image_hashes(
                session,
                hash_values=["1111111111111111", "2222222222222222"],
                user_id=42,
                created_at=now,
            )
            session.add(ImageHash(hash="3333333333333333", user_id=42, created_at=now - timedelta(hours=25)))
            await session.commit()

            deleted = await cleanup_expired_hashes(session, now=now)
            remaining = (
                await session.execute(select(ImageHash.hash).order_by(ImageHash.hash.asc()))
            ).scalars().all()

        self.assertEqual(deleted, 1)
        self.assertEqual(remaining, ["1111111111111111", "2222222222222222"])


if __name__ == "__main__":
    unittest.main()
