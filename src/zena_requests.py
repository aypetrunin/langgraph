"""Модуль реализует обращение по API."""

import asyncio
from datetime import datetime
from typing import Any

import aiohttp
import httpx

from .zena_common import retry_async
from .zena_logging import get_logger, timed

logger = get_logger()

TIMEOUT_SECONDS = 120.0


@timed("http.fetch_personal_info")
@retry_async()
async def fetch_personal_info(user_id: int) -> dict[str, Any]:
    """Получение персональной информации через API."""
    url = f"https://httpservice.ai2b.pro/v1/vk/personal-data/{user_id}"

    logger.debug("http.request", url=url)

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning("http.error", status=resp.status)
            raise RuntimeError(f"Ошибка запроса: {resp.status}")




def analyze_response(response: dict) -> list:
    """Анализирует ответ API и возвращает отфильтрованный список записей."""
    # 1. Проверяем success верхнего уровня
    if not response.get('success'):
        return []

    result = []

    # 2. Проходим по records
    for record in response.get('records', []):
        # 3. Проверяем success записи
        if not record.get('success'):
            continue

        # 4. Оставляем только статус "Ожидает..."
        if record.get('status') != 'Ожидает...':
            continue

        master = record.get('master_id', {})
        product = record.get('product', {})

        # 5. Формируем новый словарь
        result.append({
            "record_id": record.get('id'),
            "record_date": record.get('date'),
            "master_id": master.get('id'),
            "master_name": master.get('name'),
            "product_id": product.get('id'),
            "product_name": product.get('name'),
        })

    # 6. Сортировка по дате (от ранней к поздней)
    result.sort(
        key=lambda x: datetime.strptime(x["record_date"], "%Y-%m-%d %H:%M")
    )

    return result



@timed("http.fetch_personal_records")
@retry_async()
async def fetch_personal_records(
    user_companychat: int,
    channel_id: str,
) -> dict[str, Any]:
    """Получение списка услуг на которые записан клиент через API."""
    url = "https://httpservice.ai2b.pro/appointments/client/records"

    payload = {
        "user_companychat": user_companychat,
        "channel_id": channel_id
    }

    logger.debug("http.request", url=url)

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
             if resp.status == 200:
                responce = await resp.json()
                logger.debug("http.response", response=responce)
                responce_format = analyze_response(responce)
                logger.debug("http.response_formatted", data=responce_format)
                return responce_format



@timed("http.fetch_crm_go_client_info")
@retry_async()
async def fetch_crm_go_client_info(
    phone: str,
    channel_id: str = '20'
) -> dict[str, Any]:
    """Получение персональной информации через API."""
    payload = {
        "channel_id": channel_id,
        "phone": phone
    }

    url = "https://httpservice.ai2b.pro/appointments/go_crm/client_card_by_phone"

    logger.debug("http.request", url=url, payload=payload)

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
             if resp.status == 200:
                responce = await resp.json()
                logger.debug("http.response", response=responce)
                return responce

        if resp.status == 404:
            return {'success': False, 'message': "Клиента нет базе данных"}
        else:
            logger.warning("http.error", status=resp.status)
            raise RuntimeError(f"Ошибка запроса: {resp.status}")

    return {'success': False, 'message': "Клиента нет базе данных"}


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
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # async with session.post(url, json=payload, headers=headers) as resp:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                # Если сервер возвращает JSON
                return await resp.json()
    except aiohttp.ClientResponseError as e:
        logger.warning("http.error", status=e.status, message=e.message)
        raise
    except (aiohttp.ConnectionTimeoutError, aiohttp.ServerTimeoutError):
        logger.warning("http.timeout")
        raise
    except aiohttp.ClientError as e:
        logger.warning("http.client_error", error=str(e))
        raise


def get_stage_onboarding(payload: dict) -> int:
    """Определяет статус опроса по заполненности полей."""
    if not payload.get("parent_name"):  # Заполнен parent_name
        return 0

    if not payload.get("child_name"):  # Заполнен child_name
        return 1

    if not payload.get("child_date_of_birth"):  # Заполнена дата рождения
        return 2

    if not payload["contact_reason"]:  # Заполнена причина
        return 3

    return 4  # Fallback


@timed("http.fetch_masters_info")
@retry_async()
async def fetch_masters_info(channel_id: int | None = 0) -> list[dict[str, Any]]:
    """Получение списка мастеров по офисам для заданного channel_id."""
    url = "https://httpservice.ai2b.pro/appointments/yclients/staff/actual"

    OFFICE_IDS: dict[int, list[int]] = {
        1: [1, 19],
    }

    if isinstance(channel_id, int) and channel_id in OFFICE_IDS:
        office_list = OFFICE_IDS[channel_id]
    else:
        office_list = [channel_id] if isinstance(channel_id, int) and channel_id > 0 else []

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            masters_list: list[dict[str, Any]] = []

            for office_id in office_list:
                payload = {"channel_id": office_id}

                logger.debug("http.request", url=url, payload=payload)

                response = await client.post(url, json=payload)
                response.raise_for_status()
                resp_json = response.json()
                result = {
                    "office_id": office_id,
                    "masters": [
                        {
                            "master_id": s["id"],
                            "master_name": s["name"],
                            "position":s.get("position") if isinstance(s.get("position"), str)  else s.get("position", {}).get('title')
                        }
                        for s in resp_json.get("staff", [])
                    ],
                }

                masters_list.append(result)

            return masters_list

    except httpx.TimeoutException as e:
        logger.error("http.timeout", channel_id=channel_id, error=str(e))
        raise  # повторная попытка через retry_async/tenacity

    except httpx.HTTPStatusError as e:
        logger.error(
            "http.status_error",
            status=e.response.status_code,
            channel_id=channel_id,
            error=str(e),
        )
        return [{"success": False, "error": f"HTTP ошибка: {e.response.status_code}"}]

    except Exception as e:
        logger.exception(
            "http.unexpected_error",
            channel_id=channel_id,
            error=str(e),
        )
        return [{"success": False, "error": "Неизвестная ошибка при чтении мастеров"}]



async def main() -> None:
    """Точка входа для ручного тестирования запросов."""
    _phone = "799967382561"
    # response = await fetch_crm_go_client_info(
    #     phone=_phone,
    # )
    _response = await fetch_personal_records(user_companychat=145, channel_id = 1)
    # _response = await fetch_masters_info(channel_id = 21)
    # print(_response)


if __name__ == "__main__":
    asyncio.run(main())



# cd /home/copilot_superuser/petrunin/zena/langgraph
# uv run python -m src.zena_requests
