from __future__ import annotations

import io
import logging
from datetime import UTC, datetime, timedelta

import aiohttp
import imagehash
from PIL import Image, ImageOps
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImageHash, User

logger = logging.getLogger(__name__)

HASH_RETENTION_HOURS = 24


class ImageHashError(RuntimeError):
    """Raised when an uploaded image cannot be hashed."""


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def should_enforce_image_hash_guard(user: User | None, *, has_purchases: bool) -> bool:
    if user is None:
        return False
    return not has_purchases and user.generations_balance <= 2


def should_run_image_hash_check(
    image_urls: list[str],
    user: User | None,
    *,
    has_purchases: bool,
) -> bool:
    return bool(image_urls) and should_enforce_image_hash_guard(user, has_purchases=has_purchases)


def apply_duplicate_penalty(user: User) -> None:
    user.balance_free = 0
    user.generations_balance = user.balance_paid + user.balance_free


def get_hash_cutoff(now: datetime | None = None) -> datetime:
    base = now or utcnow()
    return base - timedelta(hours=HASH_RETENTION_HOURS)


async def _fetch_image_bytes(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise ImageHashError(f"Image fetch failed with status {response.status}")
            return await response.read()


def _compute_phash_from_bytes(payload: bytes) -> str:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            return str(imagehash.phash(normalized))
    except Exception as exc:  # pragma: no cover - defensive wrapper around Pillow/imagehash
        raise ImageHashError("Failed to compute image pHash") from exc


async def compute_phash_from_url(url: str) -> str:
    payload = await _fetch_image_bytes(url)
    return _compute_phash_from_bytes(payload)


async def compute_phashes_for_urls(image_urls: list[str]) -> list[str]:
    hashes: list[str] = []
    for url in image_urls:
        if not url:
            continue
        hashes.append(await compute_phash_from_url(url))
    return list(dict.fromkeys(hashes))


async def find_recent_duplicate_hash(
    session: AsyncSession,
    *,
    hash_values: list[str],
    user_id: int,
    now: datetime | None = None,
) -> ImageHash | None:
    if not hash_values:
        return None
    cutoff = get_hash_cutoff(now)
    result = await session.execute(
        select(ImageHash)
        .where(
            ImageHash.hash.in_(hash_values),
            ImageHash.user_id != user_id,
            ImageHash.created_at >= cutoff,
        )
        .order_by(ImageHash.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def store_image_hashes(
    session: AsyncSession,
    *,
    hash_values: list[str],
    user_id: int,
    created_at: datetime | None = None,
) -> None:
    stamp = created_at or utcnow()
    for hash_value in dict.fromkeys(hash_values):
        session.add(ImageHash(hash=hash_value, user_id=user_id, created_at=stamp))


async def cleanup_expired_hashes(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    cutoff = get_hash_cutoff(now)
    result = await session.execute(delete(ImageHash).where(ImageHash.created_at < cutoff))
    if commit:
        await session.commit()
    return result.rowcount or 0
