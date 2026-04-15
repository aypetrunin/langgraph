# zena_request_masters_cache.py
"""КЕШ МАСТЕРОВ (вариант B: stale-while-revalidate, обновление раз в час).

Задача:
- Данные по мастерам можно обновлять редко (раз в час).
- При этом НЕ хотим, чтобы пользователи/запросы "ждали" обновление.
- Хотим: если кеш есть (пусть даже устаревший) — сразу отдать его,
  а обновление сделать "в фоне" одним воркером.

Реализация (stale-while-revalidate):
- Храним в Redis два ключа:
  1) DATA_KEY   = masters:{channel_id}            -> JSON-данные (мастера)
  2) META_KEY   = masters:{channel_id}:meta       -> JSON {"updated_at": <epoch>}
- "Свежесть" определяется по META_KEY.updated_at (а не по TTL redis).
- DATA_KEY и META_KEY живут долго (например 7 дней), чтобы не пропасть при сбоях.
- Если данные "свежие" (updated_at < 1 час назад) -> возвращаем кеш, не обновляем.
- Если данные "протухли" (старше 1 часа) -> возвращаем кеш сразу, и:
    * пытаемся взять Redis-lock (SET NX EX)
    * если лок взяли -> запускаем обновление в фоне (asyncio.create_task)
    * если лок не взяли -> значит кто-то уже обновляет, ничего не делаем
- Если кеша нет вообще -> делаем синхронный fetch origin (и кладем в кеш).

ВАЖНО:
- В local_dev и в docker разные хосты Redis:
    docker: redis://langgraph-redis:6379 (IS_DOCKER=1)
    local:  redis://localhost:6379
- Даже если Redis недоступен, логика НЕ должна падать:
    -> fallback на in-memory кеш с тем же stale-while-revalidate подходом.
"""

import asyncio
import json
import os
import time
import uuid
from typing import Any

import httpx
import redis.asyncio as redis

from .zena_logging import get_logger, timed

logger = get_logger()

# =========================
# Настройки
# =========================
TIMEOUT_SECONDS = 120.0

# "Обновлять раз в час"
REFRESH_INTERVAL_SECONDS = 60 * 60  # 3600

# TTL хранения ключей в Redis (долго, чтобы кеш не пропадал, обновление решает meta.updated_at)
REDIS_VALUE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 дней

# Лок на обновление (должен быть больше worst-case времени fetch origin)
LOCK_TTL_SECONDS = 240  # было 60; важно > TIMEOUT_SECONDS, иначе возможны параллельные обновления

# Если Redis недоступен — in-memory fallback
MEM_VALUE_TTL_SECONDS = 24 * 60 * 60  # сутки


# =========================
# In-memory fallback cache
# =========================
# Храним: key -> (expires_at, value_json_str)
_mem_kv: dict[str, tuple[float, str]] = {}
_mem_lock = asyncio.Lock()


def _mem_get(key: str) -> str | None:
    item = _mem_kv.get(key)
    if not item:
        return None
    exp, val = item
    if exp < time.time():
        _mem_kv.pop(key, None)
        return None
    return val


def _mem_set(key: str, value: str, ttl_seconds: int) -> None:
    _mem_kv[key] = (time.time() + ttl_seconds, value)


async def _try_acquire_mem_lock() -> bool:
    """Пытаемся захватить asyncio.Lock без ожидания.

    Если занято — сразу False.
    """
    try:
        await asyncio.wait_for(_mem_lock.acquire(), timeout=0)
        return True
    except TimeoutError:
        return False


# =========================
# Redis URL resolution
# =========================
def _normalize_redis_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if "://" not in u:
        u = f"redis://{u}"
    return u


def resolve_redis_url() -> str:
    """Два режима.

    - IS_DOCKER=1: берем REDIS_URI (если задан) иначе redis://langgraph-redis:6379
    - local: берем REDIS_URI только если он адекватный, иначе redis://localhost:6379
    """
    is_docker = os.getenv("IS_DOCKER") == "1"
    raw = os.getenv("REDIS_URI")
    norm = _normalize_redis_url(raw)

    if is_docker:
        return norm or "redis://langgraph-redis:6379"

    # local: защищаемся от мусорных значений вроде fake
    if norm and norm.lower() not in {"redis://fake", "fake"}:
        return norm

    return "redis://localhost:6379"


# =========================
# Redis client (safe)
# =========================
_redis: redis.Redis | None = None


async def get_redis_safe() -> redis.Redis | None:
    """Возвращает Redis клиент или None, если Redis недоступен.

    Кеш не должен валить основную логику.
    """
    global _redis
    if _redis is not None:
        return _redis

    redis_url = resolve_redis_url()
    logger.debug("cache.redis_url", url=redis_url)

    try:
        client = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        await client.ping()
        _redis = client
        logger.info("cache.redis_connected")
        return _redis
    except Exception as e:
        logger.warning("cache.redis_unavailable", error=str(e))
        return None


# =========================
# Keys
# =========================
def _data_key(channel_id: int | None) -> str:
    return f"masters:{int(channel_id or 0)}"


def _meta_key(channel_id: int | None) -> str:
    return f"masters:{int(channel_id or 0)}:meta"


def _lock_key(channel_id: int | None) -> str:
    return f"lock:masters:{int(channel_id or 0)}"


# =========================
# Lock helpers
# =========================
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


async def _try_acquire_lock(r: redis.Redis, key: str) -> str | None:
    """Пытаемся взять лок ОДИН раз (без ожидания), потому что это фон.

    Если лок не взяли — значит кто-то уже обновляет.
    """
    token = str(uuid.uuid4())
    ok = await r.set(key, token, nx=True, ex=LOCK_TTL_SECONDS)
    return token if ok else None


async def _release_lock(r: redis.Redis, key: str, token: str) -> None:
    try:
        await r.eval(_RELEASE_LUA, 1, key, token)
    except Exception:
        pass


# =========================
# Helpers: safe parsing
# =========================
def _safe_loads_list(json_str: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(json_str)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _extract_updated_at(meta_json: str | None) -> int:
    if not meta_json:
        return 0
    try:
        v = json.loads(meta_json).get("updated_at", 0)
        return int(v) if v else 0
    except Exception:
        return 0


def _extract_position(staff_item: dict[str, Any]) -> str | None:
    """Position может приходить в разных форматах.

    - str
    - dict {"title": "..."}
    - None
    """
    pos = staff_item.get("position")
    if isinstance(pos, str):
        return pos
    if isinstance(pos, dict):
        title = pos.get("title")
        return title if isinstance(title, str) else None
    return None


# =========================
# Public API
# =========================
@timed("cache.fetch_masters_info")
async def fetch_masters_info(channel_id: int | None = 0) -> list[dict[str, Any]]:
    """Возвращает список мастеров по офисам для channel_id.

    Поведение:
    - Если данных нет -> синхронно идем в origin, кладем кеш, возвращаем.
    - Если данные есть:
        - если свежие -> сразу возвращаем.
        - если протухли -> сразу возвращаем старые и запускаем фон-обновление.
          ВАЖНО: чтобы не плодить тысячи тасок под нагрузкой, лок берём ДО create_task.
    """
    r = await get_redis_safe()

    # -------- Redis path --------
    if r is not None:
        try:
            data_key = _data_key(channel_id)
            meta_key = _meta_key(channel_id)

            cached_data = await r.get(data_key)
            cached_meta = await r.get(meta_key)

            if cached_data:
                updated_at = _extract_updated_at(cached_meta)
                age = time.time() - updated_at if updated_at else 10**18
                is_fresh = age < REFRESH_INTERVAL_SECONDS

                if is_fresh:
                    logger.info("cache.hit", channel_id=channel_id, fresh=True, age_sec=int(age))
                    return _safe_loads_list(cached_data)

                # stale: вернуть сразу, а обновление — одним воркером
                logger.info("cache.hit", channel_id=channel_id, fresh=False, age_sec=int(age))

                # --- ВАЖНО: сначала пробуем взять лок, и только если взяли — создаём таску
                lock_key = _lock_key(channel_id)
                token = await _try_acquire_lock(r, lock_key)
                if token:
                    asyncio.create_task(_refresh_in_background_redis_with_token(r, channel_id, token))

                return _safe_loads_list(cached_data)

            # MISS -> sync fetch
            logger.info("cache.miss", channel_id=channel_id)
            masters = await _fetch_origin(channel_id)
            await _write_cache_redis(r, channel_id, masters)
            return masters

        except Exception as e:
            logger.warning("cache.redis_error", error=str(e))

    # -------- Memory fallback --------
    return await _fetch_masters_memory_fallback(channel_id)


# =========================
# Redis background refresh
# =========================
async def _refresh_in_background_redis_with_token(r: redis.Redis, channel_id: int | None, token: str) -> None:
    """Фоновое обновление, лок уже взят.

    - тянем origin и перезаписываем кеш+meta
    - отпускаем лок
    """
    lock_key = _lock_key(channel_id)
    try:
        masters = await _fetch_origin(channel_id)
        await _write_cache_redis(r, channel_id, masters)
        logger.info("cache.refresh_ok", channel_id=channel_id)
    except Exception as e:
        logger.warning("cache.refresh_failed", channel_id=channel_id, error=str(e))
    finally:
        await _release_lock(r, lock_key, token)


async def _write_cache_redis(r: redis.Redis, channel_id: int | None, masters: list[dict[str, Any]]) -> None:
    """Пишем два ключа в Redis.

    - data_key: JSON с мастерами
    - meta_key: {"updated_at": epoch}
    Оба с длинным TTL (чтобы не пропадали внезапно).
    """
    data_key = _data_key(channel_id)
    meta_key = _meta_key(channel_id)

    payload = json.dumps(masters, ensure_ascii=False)
    meta = json.dumps({"updated_at": int(time.time())})

    pipe = r.pipeline(transaction=True)
    pipe.setex(data_key, REDIS_VALUE_TTL_SECONDS, payload)
    pipe.setex(meta_key, REDIS_VALUE_TTL_SECONDS, meta)
    await pipe.execute()

    logger.debug("cache.set", key=data_key, refresh_interval=REFRESH_INTERVAL_SECONDS)


# =========================
# Memory fallback (stale-while-revalidate)
# =========================
async def _fetch_masters_memory_fallback(channel_id: int | None) -> list[dict[str, Any]]:
    """Если Redis недоступен, используем in-memory fallback.

    - используем in-memory два ключа (data/meta) так же, как в Redis
    - фон-обновление делаем через asyncio.create_task с локом на уровне процесса
      (лок берём ДО create_task — чтобы не плодить таски)
    """
    data_key = _data_key(channel_id)
    meta_key = _meta_key(channel_id)

    cached_data = _mem_get(data_key)
    cached_meta = _mem_get(meta_key)

    if cached_data:
        updated_at = _extract_updated_at(cached_meta)
        age = time.time() - updated_at if updated_at else 10**18
        is_fresh = age < REFRESH_INTERVAL_SECONDS

        if is_fresh:
            logger.info("cache.hit", channel_id=channel_id, fresh=True, age_sec=int(age))
            return _safe_loads_list(cached_data)

        logger.info("cache.hit", channel_id=channel_id, fresh=False, age_sec=int(age))

        # --- ВАЖНО: сначала пробуем взять лок, и только если взяли — создаём таску
        if await _try_acquire_mem_lock():
            asyncio.create_task(_refresh_in_background_mem_locked(channel_id))

        return _safe_loads_list(cached_data)

    # MISS -> sync fetch
    logger.info("cache.miss", channel_id=channel_id)
    masters = await _fetch_origin(channel_id)
    _write_cache_mem(channel_id, masters)
    return masters


async def _refresh_in_background_mem_locked(channel_id: int | None) -> None:
    """Фоновое обновление в памяти.

    Предполагается, что _mem_lock уже захвачен (без ожидания) до create_task.
    """
    try:
        masters = await _fetch_origin(channel_id)
        _write_cache_mem(channel_id, masters)
        logger.info("cache.refresh_ok", channel_id=channel_id)
    except Exception as e:
        logger.warning("cache.refresh_failed", channel_id=channel_id, error=str(e))
    finally:
        # важно отпустить лок
        try:
            _mem_lock.release()
        except Exception:
            pass


def _write_cache_mem(channel_id: int | None, masters: list[dict[str, Any]]) -> None:
    data_key = _data_key(channel_id)
    meta_key = _meta_key(channel_id)

    payload = json.dumps(masters, ensure_ascii=False)
    meta = json.dumps({"updated_at": int(time.time())})

    _mem_set(data_key, payload, MEM_VALUE_TTL_SECONDS)
    _mem_set(meta_key, meta, MEM_VALUE_TTL_SECONDS)

    logger.debug("cache.set", key=data_key, refresh_interval=REFRESH_INTERVAL_SECONDS)


# =========================
# Origin fetch
# =========================
async def _fetch_origin(channel_id: int | None) -> list[dict[str, Any]]:
    """Реальный вызов внешнего сервиса."""
    url = "https://httpservice.ai2b.pro/appointments/yclients/staff/actual"

    OFFICE_IDS: dict[int, list[int]] = {
        1: [1, 19],
    }

    if isinstance(channel_id, int) and channel_id in OFFICE_IDS:
        office_list = OFFICE_IDS[channel_id]
    else:
        office_list = [channel_id] if isinstance(channel_id, int) and channel_id > 0 else []

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        masters_list: list[dict[str, Any]] = []

        for office_id in office_list:
            payload = {"channel_id": office_id}
            logger.debug("cache.origin_request", url=url, payload=payload)

            response = await client.post(url, json=payload)
            response.raise_for_status()
            resp_json = response.json()

            masters_list.append(
                {
                    "office_id": office_id,
                    "masters": [
                        {
                            "master_id": s.get("id"),
                            "master_name": s.get("name"),
                            "position": _extract_position(s),
                        }
                        for s in resp_json.get("staff", [])
                    ],
                }
            )

        return masters_list
