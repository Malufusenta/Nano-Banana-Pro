#!/usr/bin/env python3
"""
PostgreSQL backup → gzip → Cloudflare R2.
Запуск: python backup.py  (нужен pg_dump в PATH и переменные в .env)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

LOG = logging.getLogger("backup")

RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "7"))
R2_PREFIX = os.getenv("BACKUP_R2_PREFIX", "postgres/")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_database_url(url: str) -> dict[str, str]:
    """Разбор DATABASE_URL (postgresql+asyncpg://, postgresql://, postgres://)."""
    normalized = url.strip()
    for prefix in (
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
        "postgresql://",
        "postgres://",
    ):
        if normalized.startswith(prefix):
            normalized = "http://" + normalized[len(prefix) :]
            break
    else:
        if "://" in normalized:
            scheme, rest = normalized.split("://", 1)
            normalized = f"http://{rest}" if scheme.startswith("postgres") else normalized
        else:
            raise ValueError(f"Неподдерживаемый формат DATABASE_URL: {url[:40]}...")

    parsed = urlparse(normalized)
    if not parsed.path or parsed.path == "/":
        raise ValueError("В DATABASE_URL не указано имя базы данных")

    return {
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "dbname": parsed.path.lstrip("/").split("?")[0],
    }


def run_pg_dump(db: dict[str, str], output_path: str) -> None:
    """Создать сжатый SQL-дамп через pg_dump | gzip."""
    env = os.environ.copy()
    if db["password"]:
        env["PGPASSWORD"] = db["password"]

    cmd = [
        "pg_dump",
        "-h",
        db["host"],
        "-p",
        db["port"],
        "-U",
        db["user"],
        "-d",
        db["dbname"],
        "--no-owner",
        "--no-acl",
    ]

    LOG.info(
        "Запуск pg_dump: host=%s port=%s db=%s user=%s",
        db["host"],
        db["port"],
        db["dbname"],
        db["user"],
    )

    with open(output_path, "wb") as out_file:
        dump_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        gzip_proc = subprocess.Popen(
            ["gzip", "-c"],
            stdin=dump_proc.stdout,
            stdout=out_file,
            stderr=subprocess.PIPE,
        )
        assert dump_proc.stdout is not None
        dump_proc.stdout.close()

        _, dump_err = dump_proc.communicate()
        _, gzip_err = gzip_proc.communicate()

    if dump_proc.returncode != 0:
        stderr = dump_err.decode(errors="replace").strip()
        raise RuntimeError(f"pg_dump завершился с кодом {dump_proc.returncode}: {stderr}")
    if gzip_proc.returncode != 0:
        stderr = gzip_err.decode(errors="replace").strip()
        raise RuntimeError(f"gzip завершился с кодом {gzip_proc.returncode}: {stderr}")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    LOG.info("Дамп создан: %s (%.2f MB)", output_path, size_mb)


def get_r2_client() -> boto3.client:
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    endpoint = os.getenv("R2_ENDPOINT_URL")

    missing = [
        name
        for name, val in (
            ("R2_ACCESS_KEY_ID", access_key),
            ("R2_SECRET_ACCESS_KEY", secret_key),
            ("R2_ENDPOINT_URL", endpoint),
            ("R2_BUCKET_NAME", os.getenv("R2_BUCKET_NAME")),
        )
        if not val
    ]
    if missing:
        raise ValueError(f"Не заданы переменные окружения: {', '.join(missing)}")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def upload_to_r2(local_path: str, object_key: str) -> None:
    bucket = os.environ["R2_BUCKET_NAME"]
    client = get_r2_client()

    LOG.info("Загрузка в R2: s3://%s/%s", bucket, object_key)
    client.upload_file(local_path, bucket, object_key)
    LOG.info("Загрузка завершена успешно")


def prune_old_backups() -> None:
    """Удалить объекты в R2 старше RETENTION_DAYS дней."""
    bucket = os.environ["R2_BUCKET_NAME"]
    client = get_r2_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

    LOG.info(
        "Очистка бэкапов в R2 старше %d дн. (префикс %r)",
        RETENTION_DAYS,
        R2_PREFIX,
    )

    paginator = client.get_paginator("list_objects_v2")
    deleted = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=R2_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            last_modified = obj["LastModified"]
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)

            if last_modified < cutoff:
                client.delete_object(Bucket=bucket, Key=key)
                LOG.info("Удалён старый бэкап: %s (%s)", key, last_modified.date())
                deleted += 1

    if deleted:
        LOG.info("Удалено объектов: %d", deleted)
    else:
        LOG.info("Старых бэкапов для удаления не найдено")


def main() -> int:
    setup_logging()
    load_dotenv()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        LOG.error("DATABASE_URL не задан в окружении или .env")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.sql.gz"
    object_key = f"{R2_PREFIX}{filename}"

    tmp_dir = tempfile.mkdtemp(prefix="pg_backup_")
    local_path = os.path.join(tmp_dir, filename)

    try:
        db = parse_database_url(db_url)
        run_pg_dump(db, local_path)
        upload_to_r2(local_path, object_key)

        try:
            os.remove(local_path)
            LOG.info("Локальный файл удалён: %s", local_path)
        except OSError as exc:
            LOG.warning("Не удалось удалить %s: %s", local_path, exc)
        finally:
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

        prune_old_backups()
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        LOG.error("%s", exc)
        return 1
    except (BotoCoreError, ClientError) as exc:
        LOG.error("Ошибка R2/S3: %s", exc)
        return 1
    except subprocess.SubprocessError as exc:
        LOG.error("Ошибка subprocess: %s", exc)
        return 1

    LOG.info("Бэкап успешно завершён: %s", object_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
