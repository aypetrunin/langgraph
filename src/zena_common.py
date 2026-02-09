# zena_common.py
"""Общие утилиты без сайд-эффектов на import-time."""

import asyncio
import inspect
import logging
import random
from functools import wraps
from typing import Awaitable, Callable, TypeVar, Any

T = TypeVar("T")

logger = logging.getLogger(__name__)


def retry_async(
    retries: int = 3,
    base_delay: float = 0.5,
    backoff_factor: float = 2.0,
    jitter: float = 0.3,
    exceptions: tuple[type[Exception], ...] = (TimeoutError, ConnectionError),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Декоратор асинхронных ретраев с экспоненциальным backoff и jitter.

    Важно:
    - CancelledError НЕ ретраим (нужно для корректного shutdown/timeout).
    - По умолчанию ретраим только сетевые/временные исключения.
      Если нужно ретраить конкретные ошибки SDK/HTTP — передайте exceptions явно.

    delay = base_delay * (backoff_factor ** (attempt-1)) + uniform(0, jitter)
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Exception | None = None

            for attempt in range(1, retries + 1):
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except exceptions as e:
                    last_exc = e
                    if attempt >= retries:
                        logger.exception(
                            "Retry exhausted in %s (attempt %s/%s): %s",
                            func.__name__,
                            attempt,
                            retries,
                            type(e).__name__,
                        )
                        raise

                    wait = base_delay * (backoff_factor ** (attempt - 1)) + random.uniform(
                        0, jitter
                    )
                    logger.warning(
                        "Error in %s: %s | attempt %s/%s -> sleep %.2fs",
                        func.__name__,
                        type(e).__name__,
                        attempt,
                        retries,
                        wait,
                    )
                    await asyncio.sleep(wait)

            # теоретически не достижимо
            raise RuntimeError(f"{func.__name__}: retries exhausted") from last_exc

        return wrapper

    return decorator


def _func_name(depth: int = 0) -> str:
    """Имя функции в стеке (для отладочных логов). depth=0 текущая, 1 — вызывающая и т.д."""
    frame = inspect.currentframe()
    for _ in range(depth + 1):
        if frame is None:
            return "<unknown>"
        frame = frame.f_back
    return "<unknown>" if frame is None else frame.f_code.co_name


def _content_to_text(content: str | list[Any] | None) -> str:
    """
    Приведение контента HumanMessage в текст.
    LangGraph Studio иногда кладёт content как список частей.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list) and content:
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                txt = part.get("text")
                if isinstance(txt, str) and txt:
                    chunks.append(txt)
                    continue
                cnt = part.get("content")
                if isinstance(cnt, str) and cnt:
                    chunks.append(cnt)
        return "\n".join(chunks).strip()

    return ""
