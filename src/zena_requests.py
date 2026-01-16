"""Модуль реализует обращение по API."""

import aiohttp
import asyncio
import httpx

from typing import Any

from .zena_common import logger, retry_async


TIMEOUT_SECONDS = 120.0


@retry_async()
async def fetch_personal_info(user_id: int) -> dict[str, Any]:
    """Получение персональной информации через API."""
    logger.info("===zena_requests.fetch_personal_info===")

    url = f"https://httpservice.ai2b.pro/v1/vk/personal-data/{user_id}"

    logger.info(f"Отправка запроса: {url}")

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning(f"Ошибка запроса: {resp.status}")
            raise RuntimeError(f"Ошибка запроса: {resp.status}")


@retry_async()
async def fetch_crm_go_client_info(
    phone: str,
    channel_id: str = '20'
) -> dict[str, Any]:
    """Получение персональной информации через API."""

    logger.info("===zena_requests.fetch_crm_go_client_info===")

    payload = {
        "channel_id": channel_id,
        "phone": phone
    }

    url = f"https://httpservice.ai2b.pro/appointments/go_crm/client_card_by_phone"

    logger.info(f"Отправка запроса: {url}, payload: {payload}")

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
             if resp.status == 200:
                responce = await resp.json()
                logger.info(f"responce: {responce}")
                return responce
        
        if resp.status == 404:
            return {'success': False, 'message': "Клиента нет базе данных"}
        else:
            logger.warning(f"Ошибка запроса: {resp.status}")
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
        logger.warning(f"HTTP error: {e.status} {e.message}")
        raise
    except (aiohttp.ConnectionTimeoutError, aiohttp.ServerTimeoutError):
        logger.warning("Request timed out")
        raise
    except aiohttp.ClientError as e:
        logger.warning(f"Client error: {e}")
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

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            masters_list: list[dict[str, Any]] = []

            for office_id in office_list:
                payload = {"channel_id": office_id}

                logger.info(
                    "Отправка запроса на получение списка мастеров %s with payload=%s",
                    url,
                    payload,
                )

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
        logger.error("Таймаут при чтении мастеров channel_id=%s: %s", channel_id, e)
        raise  # повторная попытка через retry_async/tenacity

    except httpx.HTTPStatusError as e:
        logger.error(
            "Ошибка HTTP %d при чтении мастеров channel_id=%s: %s",
            e.response.status_code,
            channel_id,
            e,
        )
        return [{"success": False, "error": f"HTTP ошибка: {e.response.status_code}"}]

    except Exception as e:
        logger.exception(
            "Неожиданная ошибка при чтении мастеров channel_id=%s: %s", channel_id, e
        )
        return [{"success": False, "error": "Неизвестная ошибка при чтении мастеров"}]



async def main():
    phone = "799967382561"
    # response = await fetch_crm_go_client_info(
    #     phone=phone,
    # )

    response = await fetch_masters_info(channel_id = 21)
    print(response)


if __name__ == "__main__":
    asyncio.run(main())



# cd /home/copilot_superuser/petrunin/zena/langgraph
# uv run python -m src.zena_requests