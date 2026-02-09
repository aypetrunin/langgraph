# src/zena_resources.py
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

import asyncpg
import httpx
import redis.asyncio as redis_async

from .zena_models import close_models, init_models

logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _build_pg_dsn() -> str:
    """
    Приоритет:
      1) ZENA_PG_DSN
      2) собрать из POSTGRES_*
    """
    dsn = os.getenv("ZENA_PG_DSN")
    if dsn:
        return dsn

    user = _require_env("POSTGRES_USER")
    password = _require_env("POSTGRES_PASSWORD")
    db = _require_env("POSTGRES_DB")
    host = _require_env("POSTGRES_HOST")
    port = _require_env("POSTGRES_PORT")

    # Пароль может содержать спецсимволы — безопаснее экранировать.
    pwd = quote_plus(password)

    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def _build_http_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=float(os.getenv("ZENA_HTTP_CONNECT_TIMEOUT", "2.0")),
        read=float(os.getenv("ZENA_HTTP_READ_TIMEOUT", "5.0")),
        write=float(os.getenv("ZENA_HTTP_WRITE_TIMEOUT", "5.0")),
        pool=float(os.getenv("ZENA_HTTP_POOL_TIMEOUT", "5.0")),
    )


def _build_http_limits() -> httpx.Limits:
    return httpx.Limits(
        max_connections=int(os.getenv("ZENA_HTTP_MAX_CONN", "50")),
        max_keepalive_connections=int(os.getenv("ZENA_HTTP_MAX_KEEPALIVE", "20")),
        keepalive_expiry=float(os.getenv("ZENA_HTTP_KEEPALIVE_EXPIRY", "30.0")),
    )


@dataclass(frozen=True)
class ZenaResources:
    pg_pool: asyncpg.Pool
    http: httpx.AsyncClient
    redis: redis_async.Redis


_lock = asyncio.Lock()
_instance: Optional[ZenaResources] = None


async def init_resources() -> ZenaResources:
    """
    Инициализирует общий набор ресурсов на процесс (singleton).

    Гарантии:
    - потокобезопасно для asyncio (через lock)
    - если что-то упало в середине инициализации — корректно закрывает уже созданные ресурсы
    - поднимает LLM-модели (через zena_models.init_models), чтобы не было import-time клиентов
    """
    global _instance
    if _instance is not None:
        return _instance

    async with _lock:
        if _instance is not None:
            return _instance

        pg_pool: Optional[asyncpg.Pool] = None
        http: Optional[httpx.AsyncClient] = None
        r: Optional[redis_async.Redis] = None

        try:
            # 0) LLM models (важно: до графа/запросов; создаёт общий http client и модели)
            await init_models()

            # 1) Postgres pool
            pg_dsn = _build_pg_dsn()
            pg_pool = await asyncpg.create_pool(
                dsn=pg_dsn,
                min_size=int(os.getenv("ZENA_PG_POOL_MIN", "1")),
                max_size=int(os.getenv("ZENA_PG_POOL_MAX", "10")),
                command_timeout=float(os.getenv("ZENA_PG_COMMAND_TIMEOUT", "5")),
            )

            # 2) HTTP client (keep-alive)
            # Прокси можно задавать общим ZENA_HTTP_PROXY_URL, либо (опционально) OPENAI_PROXY_URL
            proxy = os.getenv("ZENA_HTTP_PROXY_URL") or os.getenv("OPENAI_PROXY_URL") or None

            http = httpx.AsyncClient(
                proxy=proxy,
                timeout=_build_http_timeout(),
                limits=_build_http_limits(),
            )

            # 3) Redis
            redis_url = os.getenv("ZENA_REDIS_URL", "redis://langgraph-redis:6379")
            r = redis_async.from_url(redis_url, decode_responses=True)

            _instance = ZenaResources(pg_pool=pg_pool, http=http, redis=r)

            logger.info(
                "Resources initialized (pg_pool=%s..%s, http_proxy=%s, redis=%s)",
                os.getenv("ZENA_PG_POOL_MIN", "1"),
                os.getenv("ZENA_PG_POOL_MAX", "10"),
                "set" if proxy else "none",
                redis_url,
            )
            return _instance

        except Exception:
            # ВАЖНО: rollback частичной инициализации
            logger.exception("Failed to initialize resources; rolling back partially created resources")

            if http is not None:
                try:
                    await http.aclose()
                except Exception:
                    logger.exception("Failed to close http client after init failure")

            if r is not None:
                try:
                    # В redis-py async поддержка close менялась по версиям — best effort
                    await r.aclose()
                except Exception:
                    try:
                        await r.close()  # type: ignore[attr-defined]
                    except Exception:
                        logger.exception("Failed to close redis client after init failure")

            if pg_pool is not None:
                try:
                    await pg_pool.close()
                except Exception:
                    logger.exception("Failed to close pg_pool after init failure")

            # если LLM-модели успели подняться — закроем и их
            try:
                await close_models()
            except Exception:
                logger.exception("Failed to close models after init failure")

            raise


async def close_resources() -> None:
    """
    Закрывает все ресурсы (best-effort) и сбрасывает singleton.
    Важно вызывать на shutdown приложения/воркера.
    """
    global _instance
    if _instance is None:
        # даже если ресурсы уже не подняты, модели могли быть подняты отдельно
        try:
            await close_models()
        except Exception:
            logger.exception("Failed to close models (resources not initialized)")
        return

    res = _instance
    _instance = None  # сбрасываем раньше, чтобы не было гонок повторной инициализации

    # best-effort close в обратном порядке создания
    try:
        try:
            await res.http.aclose()
        except Exception:
            logger.exception("Failed to close http client")

        try:
            try:
                await res.redis.aclose()
            except Exception:
                await res.redis.close()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Failed to close redis client")

        try:
            await res.pg_pool.close()
        except Exception:
            logger.exception("Failed to close pg_pool")

    finally:
        # модели закрываем всегда
        try:
            await close_models()
        except Exception:
            logger.exception("Failed to close models")

        logger.info("Resources closed")


async def get_resources() -> ZenaResources:
    """
    Удобный алиас: гарантирует, что ресурсы подняты.
    """
    return await init_resources()
