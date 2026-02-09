# src/zena_requests.py
"""Модуль реализует обращение по API."""

from __future__ import annotations

from typing import Any
from datetime import datetime

import httpx

from .zena_common import logger, retry_async
from .zena_resources import get_resources


# Таймауты можно задавать глобально через ZenaResources(httpx.AsyncClient),
# но если какие-то ручки "длинные" — можно переопределять per-request.
TIMEOUT_LONG_S = 120.0
TIMEOUT_MEDIUM_S = 30.0
TIMEOUT_SHORT_S = 10.0


@retry_async()
async def fetch_personal_info(user_id: int) -> dict[str, Any]:
    """Получение персональной информации через API."""
    logger.info("===zena_requests.fetch_personal_info===")

    url = f"https://httpservice.ai2b.pro/v1/vk/personal-data/{user_id}"
    logger.info("Отправка запроса: %s", url)

    res = await get_resources()
    resp = await res.http.get(url, timeout=TIMEOUT_LONG_S)

    if resp.status_code == 200:
        return resp.json()

    logger.warning("Ошибка запроса: %s %s", resp.status_code, resp.text)
    raise RuntimeError(f"Ошибка запроса: {resp.status_code}")


def analyze_response(response: dict) -> list:
    # 1. Проверяем success верхнего уровня
    if not response.get("success"):
        return []

    result = []

    # 2. Проходим по records
    for record in response.get("records", []):
        # 3. Проверяем success записи
        if not record.get("success"):
            continue

        # 4. Оставляем только статус "Ожидает..."
        if record.get("status") != "Ожидает...":
            continue

        master = record.get("master_id", {})
        product = record.get("product", {})

        # 5. Формируем новый словарь
        result.append(
            {
                "record_id": record.get("id"),
                "record_date": record.get("date"),
                "master_id": master.get("id"),
                "master_name": master.get("name"),
                "product_id": product.get("id"),
                "product_name": product.get("name"),
            }
        )

    # 6. Сортировка по дате (от ранней к поздней)
    result.sort(key=lambda x: datetime.strptime(x["record_date"], "%Y-%m-%d %H:%M"))

    return result


@retry_async()
async def fetch_personal_records(
    user_companychat: int,
    channel_id: str,
) -> list[dict[str, Any]]:
    """Получение списка услуг на которые записан клиент через API."""
    logger.info("===zena_requests.fetch_personal_records===")

    url = "https://httpservice.ai2b.pro/appointments/client/records"
    payload = {"user_companychat": user_companychat, "channel_id": channel_id}
    logger.info("Отправка запроса: %s payload=%s", url, payload)

    res = await get_resources()
    resp = await res.http.post(url, json=payload, timeout=TIMEOUT_LONG_S)

    if resp.status_code == 200:
        response_json = resp.json()
        logger.info("responce: %s", response_json)
        response_format = analyze_response(response_json)
        logger.info("responce_format: %s", response_format)
        return response_format

    logger.warning("Ошибка запроса: %s %s", resp.status_code, resp.text)
    raise RuntimeError(f"Ошибка запроса: {resp.status_code}")


@retry_async()
async def fetch_crm_go_client_info(
    phone: str,
    channel_id: str = "20",
) -> dict[str, Any]:
    """Получение данных клиента через GO CRM API."""
    logger.info("===zena_requests.fetch_crm_go_client_info===")

    payload = {"channel_id": channel_id, "phone": phone}
    url = "https://httpservice.ai2b.pro/appointments/go_crm/client_card_by_phone"
    logger.info("Отправка запроса: %s payload=%s", url, payload)

    res = await get_resources()
    resp = await res.http.post(url, json=payload, timeout=TIMEOUT_SHORT_S)

    if resp.status_code == 200:
        response_json = resp.json()
        logger.info("responce: %s", response_json)
        return response_json

    if resp.status_code == 404:
        return {"success": False, "message": "Клиента нет базе данных"}

    logger.warning("Ошибка запроса: %s %s", resp.status_code, resp.text)
    raise RuntimeError(f"Ошибка запроса: {resp.status_code}")


@retry_async()
async def sent_message_to_history(
    user_id: int,
    text: str,
    user_companychat: int,
    reply_to_history_id: int,
    access_token: str,
) -> dict[str, Any]:
    """Отправка данных по API для записи в Postgres."""
    url = "https://httpservice.ai2b.pro/v1/telegram/n8n/outgoing"
    payload = {
        "user_id": user_id,
        "text": text,
        "user_companychat": user_companychat,
        "reply_to_history_id": reply_to_history_id,
        "access_token": access_token,
    }

    res = await get_resources()

    try:
        resp = await res.http.post(url, json=payload, timeout=TIMEOUT_SHORT_S)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning("HTTP error: %s %s", e.response.status_code, e.response.text)
        raise
    except httpx.TimeoutException:
        logger.warning("Request timed out")
        raise
    except httpx.HTTPError as e:
        logger.warning("Client error: %s", e)
        raise


def get_stage_onboarding(payload: dict) -> int:
    """Определяет статус опроса по заполненности полей."""

    if not payload.get("parent_name"):
        return 0

    if not payload.get("child_name"):
        return 1

    if not payload.get("child_date_of_birth"):
        return 2

    if not payload["contact_reason"]:
        return 3

    return 4


@retry_async()
async def fetch_masters_info(channel_id: int | None = 0) -> list[dict[str, Any]]:
    """Получение списка мастеров по офисам для заданного channel_id."""
    logger.info("===get_masters===")
    logger.info("Получение списка мастеров channel_id=%s", channel_id)

    url = "https://httpservice.ai2b.pro/appointments/yclients/staff/actual"

    OFFICE_IDS: dict[int, list[int]] = {
        1: [1, 19],
    }

    if isinstance(channel_id, int) and channel_id in OFFICE_IDS:
        office_list = OFFICE_IDS[channel_id]
    else:
        office_list = [channel_id] if isinstance(channel_id, int) and channel_id > 0 else []

    res = await get_resources()

    masters_list: list[dict[str, Any]] = []
    for office_id in office_list:
        payload = {"channel_id": office_id}
        logger.info("Origin request %s payload=%s", url, payload)

        try:
            response = await res.http.post(url, json=payload, timeout=TIMEOUT_LONG_S)
            response.raise_for_status()
        except httpx.TimeoutException as e:
            logger.error("Таймаут при чтении мастеров channel_id=%s: %s", channel_id, e)
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                "Ошибка HTTP %d при чтении мастеров channel_id=%s: %s",
                e.response.status_code,
                channel_id,
                e.response.text,
            )
            return [{"success": False, "error": f"HTTP ошибка: {e.response.status_code}"}]
        except Exception as e:
            logger.exception("Неожиданная ошибка при чтении мастеров channel_id=%s: %s", channel_id, e)
            return [{"success": False, "error": "Неизвестная ошибка при чтении мастеров"}]

        resp_json = response.json()

        result = {
            "office_id": office_id,
            "masters": [
                {
                    "master_id": s.get("id"),
                    "master_name": s.get("name"),
                    "position": (
                        s.get("position")
                        if isinstance(s.get("position"), str)
                        else (s.get("position") or {}).get("title")
                    ),
                }
                for s in resp_json.get("staff", [])
            ],
        }

        masters_list.append(result)

    return masters_list


# async def main():
#     phone = "799967382561"
#     # response = await fetch_crm_go_client_info(
#     #     phone=phone,
#     # )
#     response = await fetch_personal_records(user_companychat=145, channel_id = 1)
#     # response = await fetch_masters_info(channel_id = 21)
#     # print(response)


# if __name__ == "__main__":
#     asyncio.run(main())



# cd /home/copilot_superuser/petrunin/zena/langgraph
# uv run python -m src.zena_requests