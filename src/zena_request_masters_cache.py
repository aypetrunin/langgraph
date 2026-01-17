"""
КЕШ МАСТЕРОВ (вариант B: stale-while-revalidate, обновление раз в час)

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

import os
import json
import time
import uuid
import asyncio
from typing import Any

import redis.asyncio as redis
import httpx

from .zena_common import logger


# =========================
# Настройки
# =========================
TIMEOUT_SECONDS = 120.0

# "Обновлять раз в час"
REFRESH_INTERVAL_SECONDS = 60 * 60  # 3600

# TTL хранения ключей в Redis (долго, чтобы кеш не пропадал, обновление решает meta.updated_at)
# Если хочешь — можно сделать 24*60*60 (сутки) или 7*24*60*60 (неделя)
REDIS_VALUE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 дней

# Лок на обновление (короткий)
LOCK_TTL_SECONDS = 60
LOCK_RETRY_DELAY = 0.2

# Если Redis недоступен — in-memory fallback (TTL просто "долго", обновление по updated_at)
MEM_VALUE_TTL_SECONDS = 24 * 60 * 60  # сутки (для локальной памяти нормально)


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
    """
    Два режима:
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
    """
    Возвращает Redis клиент или None, если Redis недоступен.
    Кеш не должен валить основную логику.
    """
    global _redis
    if _redis is not None:
        return _redis

    redis_url = resolve_redis_url()
    logger.info("Using Redis URL: %s", redis_url)

    try:
        client = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        await client.ping()
        _redis = client
        logger.info("Redis connected")
        return _redis
    except Exception as e:
        logger.warning("Redis unavailable (%s) -> using in-memory cache", e)
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
    """
    Пытаемся взять лок ОДИН раз (без ожидания), потому что это фон.
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
# Public API
# =========================
async def fetch_masters_info(channel_id: int | None = 0) -> list[dict[str, Any]]:
    """
    Возвращает список мастеров по офисам для channel_id.

    Поведение:
    - Если данных нет -> синхронно идем в origin, кладем кеш, возвращаем.
    - Если данные есть:
        - если свежие -> сразу возвращаем.
        - если протухли -> сразу возвращаем старые и запускаем фон-обновление.
    """
    logger.info("===get_masters===")
    logger.info("Получение списка мастеров channel_id=%s", channel_id)

    r = await get_redis_safe()

    # -------- Redis path --------
    if r is not None:
        try:
            data_key = _data_key(channel_id)
            meta_key = _meta_key(channel_id)

            cached_data = await r.get(data_key)
            cached_meta = await r.get(meta_key)

            # Если кеш есть — решаем, свежий ли он
            if cached_data:
                updated_at = 0
                if cached_meta:
                    try:
                        updated_at = int(json.loads(cached_meta).get("updated_at", 0))
                    except Exception:
                        updated_at = 0

                age = time.time() - updated_at if updated_at else 10**18
                is_fresh = age < REFRESH_INTERVAL_SECONDS

                # Всегда возвращаем кеш сразу
                if is_fresh:
                    logger.info("CACHE HIT (fresh) channel_id=%s age=%ss", channel_id, int(age))
                    return json.loads(cached_data)

                # Протух — вернем старое и обновим в фоне
                logger.info("CACHE HIT (stale) channel_id=%s age=%ss -> background refresh", channel_id, int(age))
                asyncio.create_task(_refresh_in_background_redis(r, channel_id))
                return json.loads(cached_data)

            # Кеша нет -> синхронный fetch
            logger.info("CACHE MISS channel_id=%s -> fetch origin", channel_id)
            masters = await _fetch_origin(channel_id)
            await _write_cache_redis(r, channel_id, masters)
            return masters

        except Exception as e:
            logger.warning("Redis error during cache op (%s) -> fallback to memory", e)

    # -------- Memory fallback --------
    return await _fetch_masters_memory_fallback(channel_id)


# =========================
# Redis background refresh
# =========================
async def _refresh_in_background_redis(r: redis.Redis, channel_id: int | None) -> None:
    """
    Фоновое обновление:
    - пытаемся взять лок (одна попытка)
    - если не взяли — кто-то уже обновляет, выходим
    - если взяли — тянем origin и перезаписываем кеш+meta
    """
    lock_key = _lock_key(channel_id)

    try:
        token = await _try_acquire_lock(r, lock_key)
        if token is None:
            return

        try:
            masters = await _fetch_origin(channel_id)
            await _write_cache_redis(r, channel_id, masters)
            logger.info("BACKGROUND REFRESH OK channel_id=%s", channel_id)
        finally:
            await _release_lock(r, lock_key, token)

    except Exception as e:
        # Фоновые ошибки не должны мешать запросу
        logger.warning("BACKGROUND REFRESH FAILED channel_id=%s err=%s", channel_id, e)


async def _write_cache_redis(r: redis.Redis, channel_id: int | None, masters: list[dict[str, Any]]) -> None:
    """
    Пишем два ключа:
    - data_key: JSON с мастерами
    - meta_key: {"updated_at": epoch}
    Оба с длинным TTL (чтобы не пропадали внезапно).
    """
    data_key = _data_key(channel_id)
    meta_key = _meta_key(channel_id)

    payload = json.dumps(masters, ensure_ascii=False)
    meta = json.dumps({"updated_at": int(time.time())})

    # Пишем атомарно в pipeline
    pipe = r.pipeline()
    pipe.setex(data_key, REDIS_VALUE_TTL_SECONDS, payload)
    pipe.setex(meta_key, REDIS_VALUE_TTL_SECONDS, meta)
    await pipe.execute()

    logger.info("CACHE SET key=%s refresh_interval=%ss", data_key, REFRESH_INTERVAL_SECONDS)


# =========================
# Memory fallback (stale-while-revalidate)
# =========================
async def _fetch_masters_memory_fallback(channel_id: int | None) -> list[dict[str, Any]]:
    """
    Если Redis недоступен:
    - используем in-memory два ключа (data/meta) так же, как в Redis
    - фон-обновление делаем через asyncio.create_task с локом на уровне процесса
    """
    data_key = _data_key(channel_id)
    meta_key = _meta_key(channel_id)

    cached_data = _mem_get(data_key)
    cached_meta = _mem_get(meta_key)

    if cached_data:
        updated_at = 0
        if cached_meta:
            try:
                updated_at = int(json.loads(cached_meta).get("updated_at", 0))
            except Exception:
                updated_at = 0

        age = time.time() - updated_at if updated_at else 10**18
        is_fresh = age < REFRESH_INTERVAL_SECONDS

        if is_fresh:
            logger.info("MEM CACHE HIT (fresh) channel_id=%s age=%ss", channel_id, int(age))
            return json.loads(cached_data)

        logger.info("MEM CACHE HIT (stale) channel_id=%s age=%ss -> background refresh", channel_id, int(age))
        asyncio.create_task(_refresh_in_background_mem(channel_id))
        return json.loads(cached_data)

    # MISS -> sync fetch
    logger.info("MEM CACHE MISS channel_id=%s -> fetch origin", channel_id)
    masters = await _fetch_origin(channel_id)
    _write_cache_mem(channel_id, masters)
    return masters


async def _refresh_in_background_mem(channel_id: int | None) -> None:
    """
    Фоновое обновление в памяти:
    используем _mem_lock как лок, чтобы один поток обновлял.
    """
    try:
        # попытка "не ждать": если лок занят — выходим
        if _mem_lock.locked():
            return

        async with _mem_lock:
            masters = await _fetch_origin(channel_id)
            _write_cache_mem(channel_id, masters)
            logger.info("MEM BACKGROUND REFRESH OK channel_id=%s", channel_id)

    except Exception as e:
        logger.warning("MEM BACKGROUND REFRESH FAILED channel_id=%s err=%s", channel_id, e)


def _write_cache_mem(channel_id: int | None, masters: list[dict[str, Any]]) -> None:
    data_key = _data_key(channel_id)
    meta_key = _meta_key(channel_id)

    payload = json.dumps(masters, ensure_ascii=False)
    meta = json.dumps({"updated_at": int(time.time())})

    _mem_set(data_key, payload, MEM_VALUE_TTL_SECONDS)
    _mem_set(meta_key, meta, MEM_VALUE_TTL_SECONDS)

    logger.info("MEM CACHE SET key=%s refresh_interval=%ss", data_key, REFRESH_INTERVAL_SECONDS)


# =========================
# Origin fetch
# =========================
async def _fetch_origin(channel_id: int | None) -> list[dict[str, Any]]:
    """
    Реальный вызов внешнего сервиса.
    ВАЖНО: тут намеренно нет retry_async, чтобы не поймать проблему с декоратором.
    Если хочешь ретраи — лучше обернуть именно этот кусок (тенасити/ручной retry).
    """
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
            logger.info("Origin request %s payload=%s", url, payload)

            response = await client.post(url, json=payload)
            response.raise_for_status()
            resp_json = response.json()

            masters_list.append(
                {
                    "office_id": office_id,
                    "masters": [
                        {
                            "master_id": s["id"],
                            "master_name": s["name"],
                            "position": (
                                s.get("position")
                                if isinstance(s.get("position"), str)
                                else s.get("position", {}).get("title")
                            ),
                        }
                        for s in resp_json.get("staff", [])
                    ],
                }
            )

        return masters_list



# import os
# import json
# import time
# import uuid
# import asyncio
# from typing import Any

# import redis.asyncio as redis
# import httpx

# from .zena_common import logger, retry_async

# TIMEOUT_SECONDS = 120.0
# CACHE_TTL_SECONDS = 60
# LOCK_TTL_SECONDS = 15           # сколько держим лок на время запроса
# LOCK_WAIT_SECONDS = 3           # сколько ждем лок, чтобы дождаться прогрева кеша
# LOCK_RETRY_DELAY = 0.15

# REDIS_URI = os.getenv("REDIS_URI", "redis://localhost:6379")

# _redis: redis.Redis | None = None

# def normalize_redis_url(url: str | None) -> str:
#     if not url:
#         return "redis://localhost:6379"
#     if "://" not in url:
#         return f"redis://{url}"
#     return url


# def get_redis() -> redis.Redis:
#     global _redis
#     if _redis is None:
#         raw = os.getenv("REDIS_URI")
#         redis_url = normalize_redis_url(raw)
#         logger.info("Using Redis URL: %s", redis_url)

#         _redis = redis.from_url(
#             redis_url,
#             encoding="utf-8",
#             decode_responses=True,
#         )
#     return _redis

# def _cache_key(channel_id: int | None) -> str:
#     # нормализуем None/0 как 0
#     cid = int(channel_id or 0)
#     return f"masters:{cid}"

# def _lock_key(channel_id: int | None) -> str:
#     cid = int(channel_id or 0)
#     return f"lock:masters:{cid}"


# # --- distributed lock helpers (SET NX EX + безопасный release) ---

# _RELEASE_LUA = """
# if redis.call("get", KEYS[1]) == ARGV[1] then
#   return redis.call("del", KEYS[1])
# else
#   return 0
# end
# """

# async def acquire_lock(r: redis.Redis, key: str, ttl: int, wait_seconds: float) -> str | None:
#     token = str(uuid.uuid4())
#     deadline = time.time() + wait_seconds
#     while True:
#         ok = await r.set(key, token, nx=True, ex=ttl)
#         if ok:
#             return token
#         if time.time() >= deadline:
#             return None
#         await asyncio.sleep(LOCK_RETRY_DELAY)

# async def release_lock(r: redis.Redis, key: str, token: str) -> None:
#     try:
#         await r.eval(_RELEASE_LUA, 1, key, token)
#     except Exception:
#         # не валим основной флоу из-за лока
#         pass


# async def fetch_masters_info(channel_id: int | None = 0) -> list[dict[str, Any]]:
#     logger.info("===get_masters===")
#     logger.info("Получение списка мастеров channel_id=%s", channel_id)

#     r = get_redis()
#     key = _cache_key(channel_id)

#     # 1) CACHE HIT
#     cached = await r.get(key)
#     if cached:
#         logger.info("CACHE HIT channel_id=%s", channel_id)
#         return json.loads(cached)

#     # 2) Попытка взять лок, чтобы только один процесс прогревал кеш
#     lock_key = _lock_key(channel_id)
#     token = await acquire_lock(r, lock_key, ttl=LOCK_TTL_SECONDS, wait_seconds=LOCK_WAIT_SECONDS)

#     if token is None:
#         # не смогли взять лок — возможно другой воркер уже ходит во внешний сервис
#         # подождем чуть-чуть и попробуем взять кеш ещё раз
#         cached = await r.get(key)
#         if cached:
#             logger.info("CACHE HIT(after wait) channel_id=%s", channel_id)
#             return json.loads(cached)
#         # если всё еще нет — идем сами без лока (чтобы не зависнуть)
#         logger.info("LOCK TIMEOUT, going to origin channel_id=%s", channel_id)
#         return await _fetch_and_cache(channel_id, r, key, lock_key=None, token=None)

#     try:
#         # 3) Double-check под локом (вдруг кеш успели положить, пока мы брали лок)
#         cached = await r.get(key)
#         if cached:
#             logger.info("CACHE HIT(after lock) channel_id=%s", channel_id)
#             return json.loads(cached)

#         # 4) Идем во внешний сервис и кешируем
#         return await _fetch_and_cache(channel_id, r, key, lock_key=lock_key, token=token)

#     finally:
#         await release_lock(r, lock_key, token)


# async def _fetch_and_cache(
#     channel_id: int | None,
#     r: redis.Redis,
#     cache_key: str,
#     lock_key: str | None,
#     token: str | None,
# ) -> list[dict[str, Any]]:
#     url = "https://httpservice.ai2b.pro/appointments/yclients/staff/actual"

#     OFFICE_IDS: dict[int, list[int]] = {1: [1, 19]}

#     if isinstance(channel_id, int) and channel_id in OFFICE_IDS:
#         office_list = OFFICE_IDS[channel_id]
#     else:
#         office_list = [channel_id] if isinstance(channel_id, int) and channel_id > 0 else []

#     try:
#         async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
#             masters_list: list[dict[str, Any]] = []

#             for office_id in office_list:
#                 payload = {"channel_id": office_id}
#                 logger.info("Origin request %s payload=%s", url, payload)

#                 response = await client.post(url, json=payload)
#                 response.raise_for_status()
#                 resp_json = response.json()

#                 masters_list.append({
#                     "office_id": office_id,
#                     "masters": [
#                         {
#                             "master_id": s["id"],
#                             "master_name": s["name"],
#                             "position": (
#                                 s.get("position")
#                                 if isinstance(s.get("position"), str)
#                                 else s.get("position", {}).get("title")
#                             ),
#                         }
#                         for s in resp_json.get("staff", [])
#                     ],
#                 })

#             # кешируем только успешный ответ
#             await r.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(masters_list, ensure_ascii=False))
#             logger.info("CACHE SET key=%s ttl=%ss", cache_key, CACHE_TTL_SECONDS)

#             return masters_list

#     except httpx.TimeoutException as e:
#         logger.error("Таймаут при чтении мастеров channel_id=%s: %s", channel_id, e)
#         raise

#     except httpx.HTTPStatusError as e:
#         logger.error("HTTP %d при чтении мастеров channel_id=%s: %s",
#                      e.response.status_code, channel_id, e)
#         return [{"success": False, "error": f"HTTP ошибка: {e.response.status_code}"}]

#     except Exception as e:
#         logger.exception("Неожиданная ошибка при чтении мастеров channel_id=%s: %s", channel_id, e)
#         return [{"success": False, "error": "Неизвестная ошибка при чтении мастеров"}]
