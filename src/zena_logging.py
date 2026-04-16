"""Единая настройка логирования и профилирования.

Предоставляет:
- setup_logging() — инициализация structlog (JSON в prod, цветной текст в dev).
- get_logger() — получение bound logger с контекстом из contextvars.
- timed(operation) — декоратор для профилирования async-функций.
- timed_block(operation) — контекстный менеджер для профилирования блоков кода.
- bind_contextvars / clear_contextvars — привязка user_cc к контексту запроса.

Переключение dev/prod по переменной окружения ENV (по умолчанию "prod").
В prod: JSON-формат, маскирование ПД (phone, access_token, session_id, email).
В dev: цветной ConsoleRenderer, ПД видны полностью.
"""

import contextvars
import functools
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars  # noqa: F401

# Время старта текущего запроса (графа) — для замера полного цикла
_graph_start_time: contextvars.ContextVar[float] = contextvars.ContextVar(
    "_graph_start_time",
)

# Ключи, содержащие персональные данные — маскируются в prod
SENSITIVE_KEYS = {"phone", "access_token", "session_id", "email"}


def _mask_pii_processor(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Маскирует значения чувствительных полей (prod only)."""
    for key in SENSITIVE_KEYS:
        if key in event_dict:
            val = str(event_dict[key])
            event_dict[key] = val[:3] + "***" + val[-3:] if len(val) > 6 else "***"
    return event_dict


def _noop(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Пропускает event_dict без изменений (dev — без маскирования)."""
    return event_dict


def setup_logging() -> None:
    """Настройка structlog. Вызывается один раз при старте сервиса."""
    log_format = os.getenv("LOG_FORMAT", "").strip().lower()
    env = os.getenv("ENV", "prod").strip().lower()
    is_dev = env == "dev" and log_format != "json"

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _noop if is_dev else _mask_pii_processor,
        structlog.dev.ConsoleRenderer() if is_dev
        else structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**kwargs: Any) -> structlog.stdlib.BoundLogger:
    """Возвращает bound logger с переданными начальными полями."""
    return structlog.get_logger(**kwargs)


def timed(operation: str) -> Any:
    """Декоратор: логирует duration_sec для async-функции.

    Args:
        operation: имя операции (например "postgres.fetch_channel_info").
    """
    def decorator(func: Any) -> Any:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            log = get_logger()
            t0 = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                duration = round(time.perf_counter() - t0, 3)
                log.info("operation.completed", operation=operation, duration_sec=duration)
                return result
            except Exception as e:
                duration = round(time.perf_counter() - t0, 3)
                log.error(
                    "operation.failed",
                    operation=operation,
                    duration_sec=duration,
                    error=str(e),
                )
                raise
        return wrapper
    return decorator


@asynccontextmanager
async def timed_block(operation: str) -> AsyncIterator[None]:
    """Контекстный менеджер: логирует duration_sec для блока кода.

    Args:
        operation: имя операции (например "middleware.GetDatabase").
    """
    log = get_logger()
    t0 = time.perf_counter()
    try:
        yield
    finally:
        duration = round(time.perf_counter() - t0, 3)
        log.info("operation.completed", operation=operation, duration_sec=duration)


def mark_graph_start() -> None:
    """Запоминает время старта графа в contextvars."""
    _graph_start_time.set(time.perf_counter())


def log_graph_total() -> None:
    """Логирует полное время выполнения графа."""
    t0 = _graph_start_time.get(None)
    if t0 is not None:
        duration = round(time.perf_counter() - t0, 3)
        get_logger().info(
            "operation.completed",
            operation="graph.total",
            duration_sec=duration,
        )
