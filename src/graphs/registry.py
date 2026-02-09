# src/graphs/registry.py
"""
Модуль: graphs.registry
=======================

Этот модуль реализует **процессный (in-memory) реестр LangGraph-графов** с
ленивой инициализацией и защитой от конкурентной пересборки.

-------------------------------------------------------------------------------
ЗАЧЕМ НУЖЕН РЕЕСТР
-------------------------------------------------------------------------------

В LangGraph/LangServe окружении один и тот же граф может запрашиваться много раз.
Если на каждый запрос пересоздавать:
- agent node,
- MCP tool bindings,
- внутренние структуры графа,

то это приведёт к существенным накладным расходам (latency + CPU).

Поэтому мы используем процессный кеш:
- граф по каждому ключу (persona) создаётся лениво,
- затем переиспользуется на “горячем пути”.

-------------------------------------------------------------------------------
КЛЮЧЕВЫЕ СВОЙСТВА
-------------------------------------------------------------------------------

1) Per-process cache
- кеш живёт в памяти процесса.
- в окружении с несколькими воркерами (uvicorn/gunicorn) у каждого воркера свой кеш.
- это нормальная и ожидаемая модель.

2) Concurrency safety
- конкурентные запросы одного и того же графа синхронизируются `asyncio.Lock`,
  чтобы граф создавался ровно один раз и не было гонок.

3) TTL (time-to-live)
- при `ttl_s <= 0` кеш считается бессрочным,
- при `ttl_s > 0` граф пересоздаётся, если он старше TTL.

4) Force reload
- при `force_reload=True` граф пересоздаётся всегда (удобно для dev/studio).

5) Observability (логирование)
- реестр логирует события:
  - cache_status=hit
  - cache_status=miss
  - cache_status=expired
  - cache_status=force_reload
- логирование делается через переданный `logger`, чтобы:
  - владелец модуля (entrypoint) определял logger name/level,
  - реестр оставался независимым от конкретных настроек логирования.

-------------------------------------------------------------------------------
КАК ИСПОЛЬЗОВАТЬ
-------------------------------------------------------------------------------

Владельцу нужно создать один экземпляр `GraphRegistry` на процесс:

    registry = GraphRegistry()

И затем вызывать:

    graph = await registry.aget_or_create(
        key="sofia",
        factory=lambda: create_agent_graph(port),
        ttl_s=settings.graph_cache_ttl_s,
        force_reload=settings.graph_cache_force_reload,
        logger=logger,
        port=port,
    )

`factory` должна быть async-функцией (или callable, возвращающий awaitable),
которая реально собирает граф.

-------------------------------------------------------------------------------
ВАЖНО ПРО ЛОГИ
-------------------------------------------------------------------------------

Если `logger` не передан (`None`) — логирование будет отключено (тихий режим).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Protocol, TypeVar


class _Logger(Protocol):
    def info(self, msg: str, *args, **kwargs) -> None: ...


TGraph = TypeVar("TGraph")


@dataclass
class GraphEntry:
    """Запись кеша одного графа."""
    lock: asyncio.Lock
    graph: Optional[TGraph] = None
    initialized_at: Optional[float] = None


class GraphRegistry:
    """
    In-memory реестр графов по ключу (sofia, anisa, ...).

    Обеспечивает:
    - ленивое создание,
    - защиту от конкурентного создания,
    - кеширование на процесс,
    - TTL и принудительный reload,
    - логирование cache hit/miss/expired/force_reload.
    """

    def __init__(self) -> None:
        self._items: Dict[str, GraphEntry[TGraph]] = {}

    def _entry(self, key: str) -> GraphEntry[TGraph]:
        if key not in self._items:
            self._items[key] = GraphEntry(lock=asyncio.Lock())
        return self._items[key]

    @staticmethod
    def _age_s(entry: GraphEntry[TGraph]) -> Optional[float]:
        if entry.initialized_at is None:
            return None
        return time.time() - entry.initialized_at

    @staticmethod
    def _is_fresh(entry: GraphEntry[TGraph], ttl_s: float) -> bool:
        if ttl_s <= 0:
            return True
        if entry.initialized_at is None:
            return False
        return (time.time() - entry.initialized_at) <= ttl_s

    async def aget_or_create(
        self,
        key: str,
        factory: Callable[[], Awaitable[TGraph]],
        *,
        ttl_s: float = 0.0,
        force_reload: bool = False,
        logger: Optional[_Logger] = None,
        port: Optional[int] = None,
    ) -> TGraph:
        """
        Получить граф из кеша или создать.

        Логи (если logger передан):
        - cache_status=hit          (взяли из кеша)
        - cache_status=miss         (кеша не было)
        - cache_status=expired      (TTL истёк)
        - cache_status=force_reload (принудительная пересборка)
        """
        entry = self._entry(key)
        age = self._age_s(entry)

        # Fast-path: cache hit
        if entry.graph is not None and not force_reload and self._is_fresh(entry, ttl_s):
            if logger is not None:
                logger.info(
                    "graph_cache event=graph_get key=%s cache_status=hit ttl_s=%s force_reload=%s age_s=%s mcp_port=%s",
                    key,
                    ttl_s,
                    force_reload,
                    None if age is None else round(age, 3),
                    port,
                )
            return entry.graph

        # Определяем причину пересоздания для лога (до lock)
        if force_reload:
            status = "force_reload"
        else:
            status = "miss" if entry.graph is None else "expired"

        async with entry.lock:
            # Double-check under lock
            age = self._age_s(entry)
            if entry.graph is not None and not force_reload and self._is_fresh(entry, ttl_s):
                if logger is not None:
                    logger.info(
                        "graph_cache event=graph_get key=%s cache_status=hit ttl_s=%s force_reload=%s age_s=%s mcp_port=%s",
                        key,
                        ttl_s,
                        force_reload,
                        None if age is None else round(age, 3),
                        port,
                    )
                return entry.graph

            if logger is not None:
                logger.info(
                    "graph_cache event=graph_build key=%s cache_status=%s ttl_s=%s force_reload=%s age_s=%s mcp_port=%s",
                    key,
                    status,
                    ttl_s,
                    force_reload,
                    None if age is None else round(age, 3),
                    port,
                )

            g = await factory()
            entry.graph = g
            entry.initialized_at = time.time()

            if logger is not None:
                logger.info(
                    "graph_cache event=graph_ready key=%s ttl_s=%s force_reload=%s mcp_port=%s",
                    key,
                    ttl_s,
                    force_reload,
                    port,
                )

            return g
