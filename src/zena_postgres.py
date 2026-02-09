# src/zena_postgres.py
"""Модуль реализует функции обращения к Postgres (через shared pg_pool)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager
from typing_extensions import Any, Dict, List, Iterable
from typing import AsyncIterator

import asyncpg
from .zena_resources import get_resources


import asyncpg
from dotenv import load_dotenv

from .zena_common import logger, retry_async
from .zena_requests import fetch_personal_info, fetch_personal_records
from .zena_request_masters_cache import fetch_masters_info

# ✅ новый импорт: shared resources
from .zena_resources import get_resources

# ---------------------------------------------------------------------
# local .env (как у тебя было)
# ---------------------------------------------------------------------
if not os.getenv("IS_DOCKER"):
    ROOT = Path(__file__).resolve().parents[3]
    dotenv_path = ROOT / "deploy" / "dev.env"
    load_dotenv(dotenv_path=dotenv_path)


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
@asynccontextmanager
async def pg_conn() -> AsyncIterator[asyncpg.Connection]:
    res = await get_resources()
    async with res.pg_pool.acquire() as conn:
        yield conn


async def get_weekday_info(dt: datetime | None = None) -> tuple[int, str]:
    """Асинхронно возвращает номер и название дня недели."""
    if dt is None:
        dt = datetime.now()

    weekdays_ru = (
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "пятница",
        "суббота",
        "воскресенье",
    )

    weekday_num = dt.weekday()
    return weekday_num, weekdays_ru[weekday_num]


def flatten_dict_no_prefix(d: dict[str, Any]) -> dict[str, Any]:
    """Функция получения плоского словаря."""
    items: dict[str, Any] = {}
    for key, value in d.items():
        if isinstance(value, dict):
            items.update(flatten_dict_no_prefix(value))
        else:
            items[key] = value
    return items


# ---------------------------------------------------------------------
# main collectors
# ---------------------------------------------------------------------
async def data_collection_postgres(user_companychat: int) -> dict[str, Any]:
    """Функция получения всех данных из Postgres (через pool)."""
    async with pg_conn() as conn:
        # 1) Канальный контекст
        channel_info = await fetch_channel_info(conn, user_companychat)
        channel_id = channel_info["channel_id"]
        user_id = channel_info["user_id"]

        # 2) Последовательный сбор данных (на одном conn параллелить нельзя)
        prompts_info = await fetch_prompts(conn, user_companychat)
        category = await fetch_category(conn, channel_id)
        products_full = await fetch_services(conn, channel_id)
        probny = await fetch_probny(conn, channel_id)
        first_dialog = await fetch_is_first_dialog(conn, user_companychat)

        # ✅ Эти два — сетевые, позже тоже переведём на shared http client
        masters_info = await fetch_masters_info(channel_id)
        user_info = await fetch_personal_info(user_id)

        now = datetime.now()
        weekday_num, weekday_name = await get_weekday_info(now)

        data = {
            "user_id": user_id,
            "date_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday_num": weekday_num,
            "weekday_name": weekday_name,
            **channel_info,
            "prompts_info": prompts_info,
            "category": category,
            "products_full": products_full,
            "probny": probny,
            "first_dialog": first_dialog,
            "user_info": user_info,
            "masters_info": masters_info,
        }

        flat_data = flatten_dict_no_prefix(data)
        return {"data": flat_data}


async def data_user_info(user_companychat: int) -> dict[str, Any]:
    """Функция получения данных о пользователе и компании из Postgres (через pool)."""
    async with pg_conn() as conn:
        channel_info = await fetch_channel_info(conn, user_companychat)
        user_id = channel_info["user_id"]
        user_info = await fetch_personal_info(user_id)

        data = {
            "user_id": user_id,
            "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **channel_info,
            "user_info": user_info,
        }
        flat_data = flatten_dict_no_prefix(data)
        return {"data": flat_data}


# ---------------------------------------------------------------------
# queries that already accept conn
# ---------------------------------------------------------------------
@retry_async()
async def fetch_dialog(conn: asyncpg.Connection, user_companychat: int) -> Any:
    """Получение диалога."""
    rows = await conn.fetch(
        """
        SELECT
        (CASE WHEN u.is_bot = 1 THEN 'AI' ELSE 'HUMAN' END) || ': ' || COALESCE(u.indata, '') AS message
        FROM public.bot_history u
        WHERE u.user_companychat = $1
        AND u.indata NOT IN ('стоп', 'Память очищена')
        ORDER BY u.id;
        """,
        user_companychat,
    )
    if not rows:
        return "", ""

    messages = [record["message"] for record in rows[:-1]]
    joined_messages = "\n".join(messages)
    query = rows[-1]["message"]
    return joined_messages, query


def pg_rows_to_products(rows: Iterable[asyncpg.Record]) -> list[dict[str, Any]]:
    """Преобразует asyncpg rows в формат продуктов, идентичный points_to_list."""
    result: list[dict[str, Any]] = []

    for r in rows:
        if not r:
            continue

        # asyncpg.Record поддерживает dict-like доступ
        price_min = r["price_min"]
        price_max = r["price_max"]

        result.append(
            {
                "product_id": r["product_id"],
                "product_name": r["product_name"],
                "description": r["description"],
                "duration": r["duration"],
                "price": (
                    f"{price_min} руб."
                    if price_min == price_max
                    else f"{price_min} - {price_max} руб."
                )
                if price_min is not None and price_max is not None
                else None,
            }
        )

    return result


# ---------------------------------------------------------------------
# queries that used to open new connections
# ---------------------------------------------------------------------
@retry_async()
async def fetch_key_words(channel_id: int, key_word: str) -> Any:
    """Получение ключевых фраз (через pool)."""
    async with pg_conn() as conn:
        rows = await conn.fetch(
            """
            select distinct on (p2.product_name)
                p2.channel_id,
                p2.article as product_id,
                p2.product_name,
                p2.price_min,
                p2.price_max,
                p2.time_minutes as duration,
                p2.description
            from promo p
            join products p2 on p2.product_name = p.service
            where p.channel_id = $1
              and p.key_word = $2
            order by
                p2.product_name,
                case when p2.channel_id = $1 then 0 else 1 end;
            """,
            channel_id,
            key_word,
        )

        if not rows:
            return []

        return pg_rows_to_products(rows)


@retry_async()
async def fetch_is_first_dialog(conn: asyncpg.Connection, user_companychat: int) -> bool:
    """Определение, что диалог с ботом ведётся впервые"""
    count = await conn.fetchval(
        """
        SELECT count(*)
        FROM public.bot_history 
        WHERE user_companychat = $1
        AND indata NOT IN ('стоп', 'Память очищена')
        """,
        user_companychat,
    )
    logger.info("=====fetch_is_first_dialog=====")
    logger.info("count: %s", count)
    return count == 1


@retry_async()
async def fetch_channel_info(conn: asyncpg.Connection, user_companychat: int) -> Dict[str, Any]:
    """Получение информации о компании."""
    row: asyncpg.Record | None = await conn.fetchrow(
        """
        SELECT
            cc.mcp_port AS mcp_port,
            cc2.external_id || '-' || cc.access_token as session_id,
            cc2.external_id as user_id,
            c.public_name AS public_name,
            c.description AS description,
            c.office_addresses AS office_addresses,
            c.id AS channel_id
        FROM channel c
        JOIN channel_chattype cc ON cc.channel_id = c.id
        JOIN contact_companychat ccc ON ccc.channel_chattype_id = cc.id
        JOIN contact_chattype cc2 ON ccc.user_chattype_id = cc2.id
        WHERE ccc.id = $1
        """,
        user_companychat,
    )
    return dict(row) if row else {}


@retry_async()
async def fetch_prompts(conn: asyncpg.Connection, user_companychat: int) -> Dict[str, Any]:
    """Получение промта."""
    row: asyncpg.Record | None = await conn.fetchrow(
        """
        SELECT
        (
            SELECT STRING_AGG(name, '$')
            FROM (
                SELECT DISTINCT p.id, p.name
                FROM prompt p
                JOIN channel c ON c.id = p.channel_id
                JOIN channel_chattype cc ON cc.channel_id = c.id
                JOIN contact_companychat ccc ON ccc.channel_chattype_id = cc.id
                WHERE ccc.id = $1
                  AND p.is_active = true
                ORDER BY p.id
            ) sub
        ) AS prompt_types,
        (
            SELECT STRING_AGG(description, '$')
            FROM (
                SELECT DISTINCT p.id, p.description
                FROM prompt p
                JOIN channel c ON c.id = p.channel_id
                JOIN channel_chattype cc ON cc.channel_id = c.id
                JOIN contact_companychat ccc ON ccc.channel_chattype_id = cc.id
                WHERE ccc.id = $1
                  AND p.is_active = true
                ORDER BY p.id
            ) sub
        ) AS prompt_agents;
        """,
        user_companychat,
    )

    if not row:
        return {}

    prompt_types = row["prompt_types"] or ""
    prompt_agents = row["prompt_agents"] or ""

    keys = prompt_types.split("$") if prompt_types else []
    values = prompt_agents.split("$") if prompt_agents else []

    return {k: v for k, v in zip(keys, values)}


@retry_async()
async def fetch_category(conn: asyncpg.Connection, channel_id: int) -> str:
    """Получение категорий/групп товаров/услуг."""
    rows: List[asyncpg.Record] = await conn.fetch(
        """
        SELECT DISTINCT
            p.product_unid_ean
        FROM products p
        WHERE p.product_unid_ean != 'Адрес клуба'
          AND p.channel_id = $1
        """,
        channel_id,
    )
    list_category: List[str] = [f" - {row['product_unid_ean']}" for row in rows] if rows else []
    return ", \n".join(list_category)


@retry_async()
async def fetch_services(conn: asyncpg.Connection, channel_id: int) -> str:
    """Получение товаров/услуг (для компаний с малым количеством услуг)."""
    if channel_id not in [20]:
        return []

    rows: List[asyncpg.Record] = await conn.fetch(
        """
        SELECT DISTINCT
            p.product_name,
            p.article
        FROM products p
        WHERE p.channel_id = $1
        LIMIT 6
        """,
        channel_id,
    )
    list_category: List[str] = (
        [f"{idx+1}. ID:{row['article']} - {row['product_name']}" for idx, row in enumerate(rows)]
        if rows
        else []
    )
    return ", \n".join(list_category)


@retry_async()
async def fetch_probny(conn: asyncpg.Connection, channel_id: int) -> str:
    """Получение пробных услуг."""
    rows: List[asyncpg.Record] = await conn.fetch(
        """
        SELECT
            p.product_id,
            p.product_name AS product_name,
            (p.time_minutes::text || ' минут') AS duration,
            p.price_min,
            p.price_max,
            p.description
        FROM products p
        WHERE p.product_unid_ean = 'Пробные сеансы'
          AND p.channel_id = $1
        ORDER BY p.product_id
        """,
        channel_id,
    )

    parts: List[str] = []
    for r in rows or []:
        name = (r["product_name"] or "").strip()
        duration = (r["duration"] or "").strip()
        price_min = r["price_min"]
        price_part = (
            f"{price_min}"
            if r["price_max"] in (None, price_min)
            else f"{price_min}–{r['price_max']}"
        )
        parts.append(f"Название: {name}. Продолжительность: {duration}. Стоимость: {price_part}")

    return ", ".join(parts)


# ---------------------------------------------------------------------
# mutating actions that used to open new connections
# ---------------------------------------------------------------------
@retry_async()
async def delete_history_messages(user_companychat: int) -> Dict[str, Any]:
    """Удаление истории диалога. Используется для тестирования."""
    async with pg_conn() as conn:
        channel_info = await fetch_channel_info(conn, user_companychat)
        session_id = channel_info.get("session_id")
        if not session_id:
            logger.error("Session ID not found for user_companychat=%s", user_companychat)
            return {"success": False, "error": "session_id not found"}

        try:
            async with conn.transaction():
                del_bot_history = await conn.execute(
                    "DELETE FROM public.bot_history bh WHERE bh.user_companychat = $1;",
                    user_companychat,
                )
            logger.info("Данные из истории удалены: %s", del_bot_history)
            return {"success": True}
        except Exception as e:
            logger.error("Error deleting history messages: %s", e)
            return {"success": False}


@retry_async()
async def delete_personal_data(user_companychat: int) -> Dict[str, Any]:
    """Удаление персональных данных пользователя (тестирование)."""
    logger.info("===delete_personal_data===")
    async with pg_conn() as conn:
        channel_info = await fetch_channel_info(conn, user_companychat)
        logger.info("channel_info: %s", channel_info)

        try:
            async with conn.transaction():
                user_id = await conn.fetchval(
                    """
                    select u.id
                    from contact_companychat cc
                    join "user" u on u.user_id = cc.user_chattype_id
                    where cc.id = $1
                    """,
                    user_companychat,
                )
                logger.info("user_id: %s", user_id)

                del_personal_data_consent = await conn.execute(
                    "DELETE FROM personal_data_consent WHERE user_id = $1",
                    user_id,
                )
                del_personal_data = await conn.execute(
                    "DELETE FROM personal_data WHERE user_id = $1",
                    user_id,
                )
                del_user = await conn.execute(
                    'DELETE FROM "user" WHERE id = $1',
                    user_id,
                )

            logger.info(
                "Персональные данные удалены: %s, %s, %s",
                del_personal_data_consent,
                del_personal_data,
                del_user,
            )
            return {"success": True}
        except Exception as e:
            logger.error("Error deleting personal data: %s", e)
            return {"success": False}


@retry_async()
async def save_query_from_human_in_postgres(user_companychat: int, query: str) -> bool:
    """Сохранение запроса клиента в Postgres. Необходимо при тестировании в Studio."""
    async with pg_conn() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO bot_history 
                (user_companychat, is_bot, for_user, dialog_state_id, created_at, indata)
                VALUES 
                ($1, $2, $3, $4, $5, $6)
                """,
                user_companychat,
                0,
                False,
                1,
                datetime.now(),
                query,
            )
            return True
        except Exception as e:
            logger.error("Error saving query to bot_history: %s", e)
            return False
