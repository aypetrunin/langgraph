"""Модуль реализует функции обращения к Postgres."""

import json
import os
from datetime import datetime

import asyncpg
from dotenv import load_dotenv
from typing_extensions import Any, Dict, List

from .zena_common import logger, retry_async
from .zena_requests import fetch_personal_info

load_dotenv()

POSTGRES_CONFIG = {
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
    "database": os.getenv("POSTGRES_DB"),
    "host": os.getenv("POSTGRES_HOST"),
    "port": os.getenv("POSTGRES_PORT"),
}


def flatten_dict_no_prefix(d: dict[str, Any]) -> dict[str, Any]:
    """Функция получения плоского словаря."""
    items = {}
    for key, value in d.items():
        if isinstance(value, dict):
            items.update(flatten_dict_no_prefix(value))
        else:
            items[key] = value
    return items


async def data_collection_postgres(user_companychat: int) -> dict[str, Any]:
    """Функция получения всех данных из Postgres."""
    conn = await asyncpg.connect(**POSTGRES_CONFIG)
    try:
        # 1) Канальный контекст
        channel_info = await fetch_channel_info(conn, user_companychat)
        channel_id = channel_info["channel_id"]
        session_id = channel_info["session_id"]
        user_id = channel_info["user_id"]

        # 2) Параллельный сбор зависимых данных
        # Важно: на одном соединении asyncpg нельзя выполнять несколько запросов одновременно.
        # Поэтому либо запускаем их последовательно на одном conn, либо используем пул (см. вариант 2).
        # Здесь делаем последовательную выборку, а не gather на одном conn.
        # dialog = await fetch_dialog(conn, user_companychat)
        prompts_info = await fetch_prompts(conn, user_companychat)
        dialog, query = await fetch_dialog(conn, user_companychat)
        category = await fetch_category(conn, channel_id)
        probny = await fetch_probny(conn, channel_id)
        dialog_state = await fetch_dialog_state(conn, session_id)
        product_list = await fetch_product_list(conn, session_id)
        product_id = await fetch_product_id(conn, session_id)
        user_info = await fetch_personal_info(user_id)

        data = {
            "user_id": user_id,
            "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **channel_info,
            "prompts_info": prompts_info,
            "category": category,
            "probny": probny,
            "dialog_state": dialog_state,
            "product_list": product_list,
            "product_id": product_id,
            "user_info": user_info,
            "dialog": dialog,
            "query": query,
        }
        flat_data = flatten_dict_no_prefix(data)
        return {"data": flat_data}
    finally:
        await conn.close()


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


@retry_async()
async def fetch_channel_info(
    conn: asyncpg.Connection, user_companychat: int
) -> Dict[str, Any]:
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
async def fetch_prompts(
    conn: asyncpg.Connection, user_companychat: int
) -> Dict[str, Any]:
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

    prompt_types = row["prompt_types"] if row["prompt_types"] is not None else ""
    prompt_agents = row["prompt_agents"] if row["prompt_agents"] is not None else ""

    keys = prompt_types.split("$") if prompt_types else []
    values = prompt_agents.split("$") if prompt_agents else []

    result = {k: v for k, v in zip(keys, values)}
    return result


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
        channel_id,  # без кортежа
    )
    list_category: List[str] = (
        [f" - {row['product_unid_ean']}" for row in rows] if rows else []
    )
    string_category = ", \n".join(list_category)
    return string_category


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
    # Нормализация данных и безопасная подстановка
    parts: List[str] = []
    for r in rows or []:
        name = (r["product_name"] or "").strip()
        duration = (r["duration"] or "").strip()
        price_min = r["price_min"]
        # Можно показать диапазон, если он есть
        price_part = (
            f"{price_min}"
            if r["price_max"] in (None, price_min)
            else f"{price_min}–{r['price_max']}"
        )
        piece = (
            f"Название: {name}. Продолжительность: {duration}. Стоимость: {price_part}"
        )
        parts.append(piece)
    return ", ".join(parts)


@retry_async()
async def fetch_dialog_state(conn: asyncpg.Connection, session_id: int) -> str:
    """Получение состояние диалога."""
    row: asyncpg.Record | None = await conn.fetchrow(
        """
        SELECT ds.name AS status
        FROM dialog_state ds
        WHERE ds.name IS NOT NULL
          AND ds.session_id = $1
        ORDER BY ds.id DESC
        LIMIT 1
        """,
        session_id,
    )
    status = row["status"] if row and row["status"] is not None else "new"
    # Нормализация: пустые/пробельные статусы трактуем как 'new'
    if isinstance(status, str) and not status.strip():
        return "new"
    return status


@retry_async()
async def fetch_product_list(
    conn: asyncpg.Connection, session_id: int
) -> dict[str, Any]:
    """Получение списка услуг товаров в последнем поиске."""
    row: asyncpg.Record | None = await conn.fetchrow(
        """
        SELECT
            ds.id,
            (ds.data -> 'product_search' -> 'query_search')::text AS query_search,
            (ds.data -> 'product_search' -> 'product_list')::text AS product_list
        FROM dialog_state ds
        WHERE ds.name = 'selecting'
          AND ds.session_id = $1
        ORDER BY ds.id DESC
        LIMIT 1
        """,
        session_id,
    )
    if not row:
        return {"query_search": None, "products": [], "products_text": "", "count": 0}

    # Нормализация query_search: ::text может вернуть строку в кавычках
    query_search = None
    raw_query = row["query_search"]
    if raw_query is not None:
        q = str(raw_query).strip()
        if len(q) >= 2 and q[0] == '"' and q[-1] == '"':
            q = q[1:-1]
        query_search = q

    products: list[dict[str, Any]] = []
    raw_list = row["product_list"]
    if raw_list is not None:
        raw = str(raw_list).strip()
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                for it in items:
                    products.append(
                        {
                            "product_id": it.get("product_id"),
                            "product_name": (it.get("product_name") or "").strip(),
                            "price": it.get("price"),
                        }
                    )
        except json.JSONDecodeError:
            pass

    product_list = ", ".join(
        [
            f"product_id: {p['product_id']}. Название: '{p['product_name']}'. Стоимость: {p['price']}"
            for p in products
            if p.get("product_id") is not None
            and p.get("product_name")
            and p.get("price") is not None
        ]
    )

    return {
        "query_search": query_search,
        "product_list": product_list,
    }


@retry_async()
async def fetch_product_id(conn: asyncpg.Connection, session_id: int) -> dict[str, Any]:
    """Получение id и названия выбранного клиентов товара/услуги."""
    row: asyncpg.Record | None = await conn.fetchrow(
        """
        SELECT
            ds.id,
            (ds.data -> 'product_id' -> 'product_name')::text AS product_name,
            (ds.data -> 'product_id' -> 'product_id')::text   AS product_id
        FROM dialog_state ds
        WHERE ds.name = 'record'
          AND ds.session_id = $1
        ORDER BY ds.id DESC
        LIMIT 1
        """,
        session_id,
    )
    if not row:
        return {"product_id": None, "product_name": None}

    # ::text может вернуть строку в кавычках, аккуратно снимаем их
    def normalize_text(val: str | None) -> str | None:
        """Нормальзация текста."""
        if val is None:
            return None
        s = str(val).strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            s = s[1:-1]
        return s

    product_id = normalize_text(row["product_id"])
    product_name = normalize_text(row["product_name"])

    return {"product_id": product_id, "product_name": product_name}


@retry_async()
async def delete_history_messages(user_companychat: int) -> Dict[str, Any]:
    """Удаление истории диалога. Используется для тестирования."""
    conn = await asyncpg.connect(**POSTGRES_CONFIG)
    try:
        channel_info = await fetch_channel_info(conn, user_companychat)
        session_id = channel_info.get("session_id")
        if not session_id:
            logger.error(
                f"Session ID not found for user_companychat={user_companychat}"
            )
            return {"success": False, "error": "session_id not found"}

        success = False
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM dialog_state WHERE session_id = $1", session_id
                )
                await conn.execute(
                    "DELETE FROM agent_chat_histories WHERE session_id = $1", session_id
                )
                await conn.execute(
                    """
                                    DELETE FROM bot_history
                                    WHERE user_companychat = $1
                                    AND id < (
                                        SELECT MAX(id)
                                        FROM bot_history
                                        WHERE user_companychat = $1
                                    );
                                    """,
                    user_companychat,
                )
            success = True
        except Exception as e:
            logger.error(f"Error deleting history messages: {e}")
            success = False

        return {"success": success}
    finally:
        await conn.close()
