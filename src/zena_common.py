"""Модуль реализует общие функции."""

import os
import asyncio
import inspect
import logging
import random
import httpx

from functools import wraps
from pathlib import Path
from dotenv import load_dotenv
from typing_extensions import Any, Awaitable, Callable, TypeVar

from langchain.chat_models import init_chat_model


T = TypeVar("T")

# -------------------- Logging --------------------
# Настройка логирования для вывода сообщений в консоль
logging.basicConfig(
    level=logging.INFO,  # минимальный уровень логирования INFO
    format="%(asctime)s [%(levelname)s] %(message)s",  # формат: время [уровень] сообщение
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)  # создаём логгер для текущего модуля


if not os.getenv("IS_DOCKER"):
    ROOT = Path(__file__).resolve().parents[3]
    dotenv_path = ROOT / "deploy" / "dev.env"
    load_dotenv(dotenv_path=dotenv_path)

openai_proxy = os.getenv("OPENAI_PROXY_URL")
openai_model_4o_mini = os.getenv("OPENAI_MODEL_4O_MINI")
openai_model_4o = os.getenv("OPENAI_MODEL_4O")
openai_api_key = os.getenv("OPENAI_API_KEY")
openai_api_key_reserv = os.getenv("OPENAI_API_KEY_RESERV")

model_4o_mini = init_chat_model(
    model=openai_model_4o_mini,
    api_key=openai_api_key,
    temperature=0,
    http_async_client=httpx.AsyncClient(
        proxy=openai_proxy,
        timeout=60.0
    ),
)

model_4o_mini_reserv = init_chat_model(
    model=openai_model_4o_mini,
    api_key=openai_api_key_reserv,
    temperature=0,
    http_async_client=httpx.AsyncClient(
        proxy=openai_proxy,
        timeout=60.0
    ),
)


model_4o = init_chat_model(
    model=openai_model_4o,
    api_key=openai_api_key,
    temperature=0,
    http_async_client=httpx.AsyncClient(
        proxy=openai_proxy,
        timeout=60.0
    ),
)


# -------------------- Декоратор Retry helper --------------------
def retry_async(
    retries: int = 3,
    backoff: float = 2.0,
    jitter: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Any:
    """Декоратор для асинхронных ретраев с экспоненциальным бэкоффом и равномерным джиттером.

    Args:
        retries: общее число попыток (по умолчанию 3)
        backoff: базовый коэффициент экспоненты (например, 2.0 => 2^attempt)
        jitter: амплитуда добавочного шума [0, jitter)
        exceptions: кортеж типов исключений, которые нужно ретраить

    Example:
        @retry_async()
        async def fetch_data(conn, user_id):
            return await conn.fetchrow(...)

        @retry_async(retries=5, backoff=1.5, exceptions=(asyncpg.TimeoutError,))
        async def fetch_critical_data(conn, user_id):
            return await conn.fetchrow(...)
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            for attempt in range(1, retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == retries:
                        logger.exception(
                            f"Последняя неудачная попытка {func.__name__}: {e}"
                        )
                        raise
                    wait = (backoff**attempt) + random.uniform(0, jitter)
                    logger.warning(
                        f"Ошибка в {func.__name__}: {e} | "
                        f"попытка {attempt}/{retries} — повтор через {wait:.1f}s"
                    )
                    # Неблокирующее ожидание — не мешает другим корутинам
                    await asyncio.sleep(wait)

            # Эта строка никогда не должна быть достигнута
            raise RuntimeError(f"{func.__name__}: исчерпаны все попытки")

        return wrapper

    return decorator


def _func_name(depth: int = 0) -> str:
    # depth=0 — текущая, 1 — вызывающая, 2 — её вызывающая
    frame = inspect.currentframe()
    for _ in range(depth + 1):
        if frame is None:
            return "<unknown>"
        frame = frame.f_back
    if frame is None:
        return "<unknown>"
    return frame.f_code.co_name


def _content_to_text(content: str | list[Any] | None) -> str:
    """Функция получения сообщения.

    Функция возвращает content из HumanMessages в зависимости от того
    где оно было сформировано Langgraph Studio в закладке Chat или Graph.
    Особенность Langgraph Studio.
    """
    # logger.info("_content_to_text")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        part = content[0]
        if isinstance(part, dict):
            if "text" in part and isinstance(part["text"], str):
                return part["text"]
            if "content" in part and isinstance(part["content"], str):
                return part["content"]
    return ""
