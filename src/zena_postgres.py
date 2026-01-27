"""Модуль реализует функции обращения к Postgres."""

import json
import os
from datetime import datetime

import asyncpg
from dotenv import load_dotenv
from pathlib import Path
from typing_extensions import Any, Dict, List, Iterable

from .zena_common import logger, retry_async
from .zena_requests import fetch_personal_info, fetch_personal_records
from .zena_request_masters_cache import fetch_masters_info

if not os.getenv("IS_DOCKER"):
    ROOT = Path(__file__).resolve().parents[3]
    dotenv_path = ROOT / "deploy" / "dev.env"
    load_dotenv(dotenv_path=dotenv_path)


POSTGRES_CONFIG = {
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
    "database": os.getenv("POSTGRES_DB"),
    "host": os.getenv("POSTGRES_HOST"),
    "port": os.getenv("POSTGRES_PORT"),
}

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

        # 2) Последовательный сбор данных
        prompts_info = await fetch_prompts(conn, user_companychat)
        category = await fetch_category(conn, channel_id)
        products_full = await fetch_services(conn, channel_id)
        probny = await fetch_probny(conn, channel_id)
        first_dialog = await fetch_is_first_dialog(conn, user_companychat)
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

    finally:
        await conn.close()



# async def data_collection_postgres(user_companychat: int) -> dict[str, Any]:
#     """Функция получения всех данных из Postgres."""
#     conn = await asyncpg.connect(**POSTGRES_CONFIG)
#     try:
#         # 1) Канальный контекст
#         channel_info = await fetch_channel_info(conn, user_companychat)
#         channel_id = channel_info["channel_id"]
#         session_id = channel_info["session_id"]
#         user_id = channel_info["user_id"]

#         # 2) Параллельный сбор зависимых данных
#         # Важно: на одном соединении asyncpg нельзя выполнять несколько запросов одновременно.
#         # Поэтому либо запускаем их последовательно на одном conn, либо используем пул (см. вариант 2).
#         # Здесь делаем последовательную выборку, а не gather на одном conn.
#         prompts_info = await fetch_prompts(conn, user_companychat)
#         category = await fetch_category(conn, channel_id)
#         products_full = await fetch_services(conn, channel_id)
#         probny = await fetch_probny(conn, channel_id)
#         first_dialog = await fetch_is_first_dialog(conn, user_companychat)
#         masters_info = await fetch_masters_info(channel_id)
#         user_info = await fetch_personal_info(user_id)
#         # user_records = await fetch_personal_records(user_companychat, channel_id)

#         data = {
#             "user_id": user_id,
#             "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
#             **channel_info,
#             "prompts_info": prompts_info,
#             "category": category,
#             "products_full" : products_full,
#             "probny": probny,
#             "first_dialog": first_dialog,
#             "user_info": user_info,
#             # "user_records": user_records,
#             "masters_info": masters_info,
#         }
#         flat_data = flatten_dict_no_prefix(data)
#         return {"data": flat_data}
#     finally:
#         await conn.close()

async def data_user_info(user_companychat: int) -> dict[str, Any]:
    """Функция получения данных о пользователе и компании из Postgres."""
    conn = await asyncpg.connect(**POSTGRES_CONFIG)
    try:
        # 1) Канальный контекст
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



def pg_rows_to_products(rows: Iterable[asyncpg.Record]) -> list[dict[str, Any]]:
    """Преобразует asyncpg rows в формат продуктов, идентичный points_to_list."""
    result: list[dict[str, Any]] = []

    for r in rows:
        if not r:
            continue

        price_min = r.get("price_min")
        price_max = r.get("price_max")

        result.append({
            "product_id": r.get("product_id"),
            "product_name": r.get("product_name"),
            "description": r.get("description"),
            "duration": r.get("duration"),
            "price": (
                f"{price_min} руб."
                if price_min == price_max
                else f"{price_min} - {price_max} руб."
            )
            if price_min is not None and price_max is not None
            else None,
        })

    return result


@retry_async()
async def fetch_key_words(channel_id: int, key_word: str) -> Any:
    """Получение ключевых фраз"""
    conn = await asyncpg.connect(**POSTGRES_CONFIG)
    try:
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
            channel_id, key_word
        )

        if not rows:
            return []

        return pg_rows_to_products(rows)
    finally:
        await conn.close()


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
    logger.info(f"=====fetch_is_first_dialog=====")
    logger.info(f"count: {count}")
    return count == 1


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
async def fetch_services(conn: asyncpg.Connection, channel_id: int) -> str:
    """Получение товаров/услуг. Это нужно для компаний с малым количеством услуг.
    Для примера Алена с количеством услуг 5 шт. channel_id=20"""

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
        channel_id,  # без кортежа
    )
    list_category: List[str] = (
        [f"{idx+1}. ID:{row['article']} - {row['product_name']}" for idx, row in enumerate(rows)] if rows else []
    )
    string_services = ", \n".join(list_category)
    return string_services


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

                del_bot_history  = await conn.execute(
                    "DELETE FROM public.bot_history bh WHERE bh.user_companychat = $1;",
                    user_companychat
                )
            success = True
            logger.info(f"Данные из истории удалены: {del_bot_history}")
        except Exception as e:
            logger.error(f"Error deleting history messages: {e}")
            success = False

        return {"success": success}
    finally:
        await conn.close()


@retry_async()
async def delete_personal_data(user_companychat: int) -> Dict[str, Any]:
    """Удаление истории диалога. Используется для тестирования."""
    logger.info("===delete_personal_data===")
    conn = await asyncpg.connect(**POSTGRES_CONFIG)
    try:
        channel_info = await fetch_channel_info(conn, user_companychat)
        logger.info(f"channel_info: {channel_info}")
        success = False
        try:
            async with conn.transaction():
                user_id = await conn.fetchval(
                    '''
                    select u.id
                    from contact_companychat cc
                    join "user" u on u.user_id = cc.user_chattype_id
                    where cc.id = $1
                    ''',
                    user_companychat,
                )
                logger.info("user_id: %s", user_id)
                del_personal_data_consent = await conn.execute(
                    "DELETE FROM personal_data_consent WHERE user_id = $1", user_id
                )
                del_personal_data = await conn.execute(
                    "DELETE FROM personal_data WHERE user_id = $1", user_id
                )
                del_user= await conn.execute(
                    'DELETE FROM "user" WHERE id = $1', user_id
                )

            success = True
            logger.info(f"Персональные данные удалены: {del_personal_data_consent}, {del_personal_data}, {del_user}")
        except Exception as e:
            logger.error(f"Error deleting history messages: {e}")
            success = False

        return {"success": success}
    finally:
        await conn.close()



@retry_async()
async def save_query_from_human_in_postgres(user_companychat: int, query: str) -> bool:
    """Сохранение запроса клиента в Postgres. Необходимо при тестировании в Studio."""
    conn = await asyncpg.connect(**POSTGRES_CONFIG)
    try:
        await conn.execute(
            """
            INSERT INTO bot_history 
            (user_companychat, is_bot, for_user, dialog_state_id, created_at, indata)
            VALUES 
            ($1, $2, $3, $4, $5, $6)
            """,
            user_companychat,  # $1
            0,                 # $2
            False,             # $3
            1,                 # $4
            datetime.now(),    # $5
            query              # $6
        )
        success = True
    except Exception as e:
        logger.error(f"Error saving query to bot_history: {e}")
        success = False
    finally:
        await conn.close()
        return success