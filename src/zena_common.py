import logging
import random
import time
import asyncio
import random
import inspect

from functools import wraps

from typing_extensions import Any, Awaitable, Callable, TypeVar, Union

T = TypeVar("T")

# -------------------- Logging --------------------
# Настройка логирования для вывода сообщений в консоль
logging.basicConfig(
    level=logging.INFO,  # минимальный уровень логирования INFO
    format="%(asctime)s [%(levelname)s] %(message)s",  # формат: время [уровень] сообщение
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)  # создаём логгер для текущего модуля

# -------------------- Декоратор Retry helper --------------------
def retry_async(
    retries: int = 3,
    backoff: float = 2.0,
    jitter: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """
    Декоратор для асинхронных ретраев с экспоненциальным бэкоффом и равномерным джиттером.
    
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
                    wait = (backoff ** attempt) + random.uniform(0, jitter)
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

# def retry_async(
#     retries: int = 3,
#     backoff: float = 2.0,
#     jitter: float = 1.0,
#     exceptions: tuple[type[Exception], ...] = (Exception,),
# ):
#     """
#     Декоратор для асинхронных ретраев с экспоненциальным бэкоффом.
#     """
#     def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
#         @wraps(func)
#         async def wrapper(*args: Any, **kwargs: Any) -> T:
#             for attempt in range(1, retries + 1):
#                 try:
#                     return await func(*args, **kwargs)
#                 except exceptions as e:
#                     if attempt == retries:
#                         logger.exception(f"Последняя неудачная попытка {func.__name__}: {e}")
#                         raise
#                     wait = (backoff ** attempt) + random.uniform(0, jitter)
#                     logger.warning(
#                         f"Ошибка в {func.__name__}: {e} | попытка {attempt}/{retries} — "
#                         f"повтор через {wait:.1f}s"
#                     )
#                     await asyncio.sleep(wait)
#             raise RuntimeError("Не должно быть достигнуто")
#         return wrapper
#     return decorator

# def retry_request(
#     func: Callable,
#     *args,
#     retries: int = 3,
#     backoff: float = 2.0,
#     **kwargs
# ):
#     for attempt in range(1, retries + 1):
#         try:
#             return func(*args, **kwargs)
#         except Exception as e:
#             if attempt == retries:
#                 logger.exception(f"Последняя неудачная попытка {func.__name__}: {e}")
#                 raise
#             wait = backoff ** attempt + random.uniform(0, 1)
#             logger.warning(
#                 f"Ошибка в {func.__name__}: {e} | попытка {attempt}/{retries} — повтор через {wait:.1f}s"
#             )
#             time.sleep(wait)


# async def retry_async(
#     func: Callable[..., Awaitable[T]],
#     *args: Any,
#     retries: int = 3,
#     backoff: float = 2.0,
#     jitter: float = 1.0,
#     exceptions: tuple[type[Exception], ...] = (Exception,),
#     **kwargs: Any
# ) -> T:
#     """
#     Асинхронные ретраи с экспоненциальным бэкоффом и равномерным джиттером.
#     - func: async-функция, которую ретраим
#     - retries: общее число попыток
#     - backoff: базовый коэффициент экспоненты (например, 2.0 => 2^attempt)
#     - jitter: амплитуда добавочного шума [0, jitter)
#     - exceptions: кортеж типов исключений, которые нужно ретраить
#     """
#     for attempt in range(1, retries + 1):
#         try:
#             return await func(*args, **kwargs)
#         except exceptions as e:
#             if attempt == retries:
#                 logger.exception(f"Последняя неудачная попытка {getattr(func, '__name__', func)}: {e}")
#                 raise
#             wait = (backoff ** attempt) + random.uniform(0, jitter)
#             logger.warning(
#                 f"Ошибка в {getattr(func, '__name__', func)}: {e} | попытка {attempt}/{retries} — "
#                 f"повтор через {wait:.1f}s"
#             )
#             # Неблокирующее ожидание — не мешает другим корутинам
#             await asyncio.sleep(wait)


def _func_name(depth: int = 0) -> str:
    # depth=0 — текущая, 1 — вызывающая, 2 — её вызывающая
    frame = inspect.currentframe()
    for _ in range(depth + 1):
        if frame is None:
            return "<unknown>"
        frame = frame.f_back
    return frame.f_code.co_name


def _content_to_text(content: Union[str, list, None]) -> str:
    """
    Функция возвращает content из HumanMessages в зависимости от того
    где оно было сформировано Langgraph Studio в закладке Chat или Graph.
    Особенность Langgraph Studio.
    """
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