
# src/zena_create_graph.py
"""
Модуль: zena_create_graph
========================

Этот модуль является **точкой входа LangGraph runtime** и отвечает за создание
LangGraph-графов (workflow) для разных компаний / персон
(sofia, anisa, annitta, anastasia, alena, valentina, marina, ...).

-------------------------------------------------------------------------------
КОНТЕКСТ: ЗАЧЕМ ЭТО НУЖНО
-------------------------------------------------------------------------------

У нас есть несколько “персон” (или компаний). Для каждой персоны агент:
- использует одинаковую бизнес-логику (одинаковая структура графа),
- но подключается к своему MCP-серверу (чаще всего отличается **портом**).

Цель модуля — дать LangGraph/LangServe runtime функции вида:

    make_graph_sofia()
    make_graph_anisa()
    ...

которые возвращают готовый, скомпилированный LangGraph.

-------------------------------------------------------------------------------
ОБЩАЯ ИДЕЯ / СТРУКТУРА ГРАФА
-------------------------------------------------------------------------------

Для каждой персоны создаётся граф одинаковой структуры:

    START → "agent" → END

Граф состоит из одного узла `agent`.

Узел `agent`:
- принимает вход `InputState`,
- работает со `State` и `Context`,
- возвращает `OutputState`,
- внутри себя использует MCP-инструменты и LLM-модели,
- MCP инструменты доступны на определённом порту (у каждой персоны свой).

Создание узла агента делегировано в:

    create_agent_mcp(mcp_port=...)

-------------------------------------------------------------------------------
RUNTIME / ОКРУЖЕНИЕ (.env) — КРИТИЧЕСКИ ВАЖНО
-------------------------------------------------------------------------------

⚠️ КРИТИЧЕСКИ ВАЖНО ⚠️

Этот модуль **первым делом** вызывает `init_runtime()`.

Зачем:
- во многих местах далее используется `os.getenv()`,
- конфигурация зависит от переменных окружения (порты MCP, TTL кеша и т.д.),
- если `.env` не будет загружен до чтения переменных — значения конфигурации могут
  быть неверными и “залипнуть” до перезапуска процесса (типичный import-time эффект).

Правило:
- `init_runtime()` должен быть вызван **до любых обращений к os.getenv()**.

Поведение `init_runtime()` (по договорённости проекта):
- В Docker-окружении `.env` обычно НЕ загружается (env приходит снаружи).
- Локально:
  - поддерживаются `ENV`, `ENV_FILE`,
  - загружается `deploy/dev.env` или `deploy/prod.env`,
  - переменные окружения не переопределяются (`override=False`).

-------------------------------------------------------------------------------
SETTINGS (runtime.settings)
-------------------------------------------------------------------------------

Настройки вынесены в отдельный модуль `src/runtime/settings.py`.

Ключевые моменты:
- `get_settings()` кешируется (LRU, maxsize=1) и читается **один раз на процесс**.
- TTL/force-reload управляют пересозданием графа, но НЕ перечитывают env.
- изменения env на лету не подхватятся без рестарта воркера.

-------------------------------------------------------------------------------
OBSERVABILITY (runtime.logging + cache logs)
-------------------------------------------------------------------------------

Чтобы быстро понимать, что происходит в проде/деве, модуль логирует события кеша
графов. Логи включают:
- имя графа (persona),
- cache_status: hit|miss|expired|force_reload
- ttl_s, force_reload,
- возраст кеша (age_s) если есть,
- порт MCP, который используется для сборки графа.

Конфиг логирования вынесен в:

    src/runtime/logging.py

Управление через env:
- GRAPH_CACHE_LOG_LEVEL=INFO   # DEBUG/INFO/WARNING/ERROR
- GRAPH_CACHE_LOGGER_NAME=zena.graph_cache

-------------------------------------------------------------------------------
GRAPH REGISTRY (graphs.registry)
-------------------------------------------------------------------------------

Логика процессного кеша/реестра графов вынесена в отдельный модуль:

    src/graphs/registry.py

Там находятся:
- `GraphRegistry`  — реестр с TTL/force-reload + lock
- `GraphEntry`     — запись кеша

`zena_create_graph` создаёт один экземпляр реестра `_registry` и использует его
для всех `make_graph_*`.

-------------------------------------------------------------------------------
FAIL-FAST SHARED РЕСУРСЫ
-------------------------------------------------------------------------------

Перед созданием графа вызывается:

    await init_resources()

Это гарантирует:
- ленивую инициализацию shared-ресурсов:
  - Postgres pool
  - Redis
  - HTTP client
  - LLM models
- ошибки конфигурации (DSN, URL, ключи) проявляются **сразу**,
  а не глубоко внутри middleware или agent logic.

Важно:
- Это НЕ прогрев всех графов на старте контейнера.
- Ресурсы создаются только при первом реальном создании графа (т.е. при первом запросе).

-------------------------------------------------------------------------------
ПУБЛИЧНЫЕ ФАБРИКИ ДЛЯ LANGGRAPH RUNTIME
-------------------------------------------------------------------------------

LangGraph runtime (через `langgraph.json` / `LANGSERVE_GRAPHS`) ожидает
вида "module:function".

Поэтому экспортируются функции:

    make_graph_sofia
    make_graph_anisa
    ...

Внутри они вызывают общую фабрику:

    _make_graph("sofia")

которая:
- берёт настройки из `get_settings()`,
- выбирает порт по имени,
- забирает/создаёт граф из `_registry` (с TTL/force-reload),
- возвращает `CompiledStateGraph`.

-------------------------------------------------------------------------------
КАК ДЕБАЖИТЬ CACHE / TTL / FORCE-RELOAD (ОЖИДАЕМОЕ ПОВЕДЕНИЕ)
-------------------------------------------------------------------------------

- Первый вызов make_graph_* в воркере:
  граф создаётся, init_resources() срабатывает, agent создаётся, граф кешируется.

- Повторный вызов (TTL=0 и FORCE_RELOAD=0):
  граф берётся из кеша, init_resources() и создание agent НЕ повторяются.

- TTL истёк:
  граф пересобирается (под lock), в кеш кладётся новая версия.

- FORCE_RELOAD=1:
  граф пересобирается на каждый запрос (дороже, но удобно для dev).

Важно:
- TTL/force-reload пересоздают граф,
  но НЕ перечитывают env (get_settings() уже закеширован) — для этого нужен рестарт воркера.

-------------------------------------------------------------------------------
ДЛЯ НОВИЧКА (ОБЪЯСНЕНИЕ ПРОСТЫМИ СЛОВАМИ)
-------------------------------------------------------------------------------

Представь, что у тебя есть несколько “ботов” — София, Алена, Валентина.
У каждого бота одинаковая логика общения (одинаковый граф),
но каждый бот подключается к своему “набору инструментов” (MCP server) по своему порту.

Этот файл делает три главные вещи:

1) **Готовит окружение**:
   - вызывает init_runtime(), чтобы переменные окружения (.env) были загружены вовремя.

2) **Создаёт граф**:
   - при первом запросе создаёт агента (agent) через create_agent_mcp,
   - строит минимальный граф START → agent → END,
   - компилирует граф.

3) **Кэширует граф**:
   - чтобы не создавать агента заново на каждый запрос,
   - хранит готовый граф в памяти процесса (через GraphRegistry),
   - при одновременных запросах защищает создание локом,
   - при необходимости умеет пересоздавать (TTL) или всегда пересоздавать (force reload).
"""

from __future__ import annotations

# ------------------------------------------------------------------------------
# Runtime init (ДОЛЖЕН быть выполнен до os.getenv и любых вычислений по env)
# ------------------------------------------------------------------------------
from .zena_runtime import init_runtime

init_runtime()

# ------------------------------------------------------------------------------
# Imports
# ------------------------------------------------------------------------------
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .graphs.registry import GraphRegistry
from .runtime.logging import get_graph_cache_logger
from .runtime.settings import get_settings
from .zena_create_agent import create_agent_mcp
from .zena_resources import init_resources
from .zena_state import Context, InputState, OutputState, State
from .graphs.factory import create_agent_graph



# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
logger = get_graph_cache_logger()


# ------------------------------------------------------------------------------
# Graph registry (per-process lazy cache)
# ------------------------------------------------------------------------------
_registry: GraphRegistry[CompiledStateGraph] = GraphRegistry()


# ------------------------------------------------------------------------------
# Public graph factories (used by LangGraph CLI / runtime)
# ------------------------------------------------------------------------------
async def _make_graph(name: str) -> CompiledStateGraph:
    s = get_settings()
    try:
        port = s.mcp_ports[name]
    except KeyError as e:
        raise RuntimeError(
            f"Unknown graph key {name!r}. Available: {sorted(s.mcp_ports.keys())}"
        ) from e

    return await _registry.aget_or_create(
        name,
        lambda: create_agent_graph(port),
        ttl_s=s.graph_cache_ttl_s,
        force_reload=s.graph_cache_force_reload,
        logger=logger,
        port=port,
    )


async def make_graph_sofia() -> CompiledStateGraph:
    return await _make_graph("sofia")


async def make_graph_anisa() -> CompiledStateGraph:
    return await _make_graph("anisa")


async def make_graph_annitta() -> CompiledStateGraph:
    return await _make_graph("annitta")


async def make_graph_anastasia() -> CompiledStateGraph:
    return await _make_graph("anastasia")


async def make_graph_alena() -> CompiledStateGraph:
    return await _make_graph("alena")


async def make_graph_valentina() -> CompiledStateGraph:
    return await _make_graph("valentina")


async def make_graph_marina() -> CompiledStateGraph:
    return await _make_graph("marina")



# # src/zena_create_graph.py
# """
# Модуль: zena_create_graph
# ========================

# Этот модуль является **точкой входа LangGraph runtime** и отвечает за создание
# LangGraph-графов (workflow) для разных компаний / персон
# (sofia, anisa, annitta, anastasia, alena, valentina, marina, ...).

# -------------------------------------------------------------------------------
# КОНТЕКСТ: ЗАЧЕМ ЭТО НУЖНО
# -------------------------------------------------------------------------------

# У нас есть несколько “персон” (или компаний). Для каждой персоны агент:
# - использует одинаковую бизнес-логику (одинаковая структура графа),
# - но подключается к своему MCP-серверу (чаще всего отличается **портом**).

# Цель модуля — дать LangGraph/LangServe runtime функции вида:

#     make_graph_sofia()
#     make_graph_anisa()
#     ...

# которые возвращают готовый, скомпилированный LangGraph.

# -------------------------------------------------------------------------------
# ОБЩАЯ ИДЕЯ / СТРУКТУРА ГРАФА
# -------------------------------------------------------------------------------

# Для каждой персоны создаётся граф одинаковой структуры:

#     START → "agent" → END

# Граф состоит из одного узла `agent`.

# Узел `agent`:
# - принимает вход `InputState`,
# - работает со `State` и `Context`,
# - возвращает `OutputState`,
# - внутри себя использует MCP-инструменты и LLM-модели,
# - MCP инструменты доступны на определённом порту (у каждой персоны свой).

# Создание узла агента делегировано в:

#     create_agent_mcp(mcp_port=...)

# -------------------------------------------------------------------------------
# RUNTIME / ОКРУЖЕНИЕ (.env) — КРИТИЧЕСКИ ВАЖНО
# -------------------------------------------------------------------------------

# ⚠️ КРИТИЧЕСКИ ВАЖНО ⚠️

# Этот модуль **первым делом** вызывает `init_runtime()`.

# Зачем:
# - во многих местах далее используется `os.getenv()`,
# - конфигурация зависит от переменных окружения (порты MCP, TTL кеша и т.д.),
# - если `.env` не будет загружен до чтения переменных — значения конфигурации могут
#   быть неверными и “залипнуть” до перезапуска процесса (типичный import-time эффект).

# Правило:
# - `init_runtime()` должен быть вызван **до любых обращений к os.getenv()**.

# Поведение `init_runtime()` (по договорённости проекта):
# - В Docker-окружении `.env` обычно НЕ загружается (env приходит снаружи).
# - Локально:
#   - поддерживаются `ENV`, `ENV_FILE`,
#   - загружается `deploy/dev.env` или `deploy/prod.env`,
#   - переменные окружения не переопределяются (`override=False`).

# -------------------------------------------------------------------------------
# SETTINGS (КОНФИГ НА-DEMAND, БЕЗ IMPORT-TIME "ЗАЛИПАНИЯ")
# -------------------------------------------------------------------------------

# В этом модуле конфигурация собирается через `get_settings()` (кешируется LRU на процесс),
# а не через import-time глобальные константы.

# Что хранится в Settings:
# - `mcp_ports`: мапа { persona_name -> port }
# - `graph_cache_ttl_s`: TTL для процессного кеша графов
# - `graph_cache_force_reload`: принудительное пересоздание графа на каждый запрос

# Env-переменные (пример):
# - MCP_PORT_SOFIA=5002
# - MCP_PORT_ANISA=5005
# - ...
# - GRAPH_CACHE_TTL_S=0            # 0 = бессрочно (поведение “как раньше”)
# - GRAPH_CACHE_FORCE_RELOAD=0     # 1 = пересоздавать каждый раз (удобно для dev/studio)

# Важно про кеширование Settings:
# - `get_settings()` читается **один раз на процесс** (LRU cache).
# - Изменения env на лету НЕ подхватятся без рестарта процесса/воркера.
# - TTL/force-reload управляют пересозданием графа, но НЕ перечитывают env.

# Почему так лучше:
# - избегаем import-time вычислений, которые могут “зацементировать” неправильные значения,
# - получаем единый центр настроек и возможность расширять конфиг.

# -------------------------------------------------------------------------------
# OBSERVABILITY (ЛОГИ CACHE HIT/MISS/EXPIRED)
# -------------------------------------------------------------------------------

# Чтобы быстро понимать, что происходит в проде/деве, модуль логирует события кеша
# графов. Логи включают:
# - имя графа (persona),
# - cache_status: hit|miss|expired|force_reload
# - ttl_s, force_reload,
# - возраст кеша (age_s) если есть,
# - порт MCP, который используется для сборки графа.

# Управление логированием через env:
# - GRAPH_CACHE_LOG_LEVEL=INFO   # DEBUG/INFO/WARNING/ERROR
# - GRAPH_CACHE_LOGGER_NAME=zena.graph_cache

# -------------------------------------------------------------------------------
# FAIL-FAST SHARED РЕСУРСЫ
# -------------------------------------------------------------------------------

# Перед созданием графа вызывается:

#     await init_resources()

# Это гарантирует:
# - ленивую инициализацию shared-ресурсов:
#   - Postgres pool
#   - Redis
#   - HTTP client
#   - LLM models
# - ошибки конфигурации (DSN, URL, ключи) проявляются **сразу**,
#   а не глубоко внутри middleware или agent logic.

# Важно:
# - Это НЕ прогрев всех графов на старте контейнера.
# - Ресурсы создаются только при первом реальном создании графа (т.е. при первом запросе).

# -------------------------------------------------------------------------------
# ЛЕНИВЫЙ КЕШ ГРАФОВ (PER-PROCESS) + TTL / FORCE-RELOAD
# -------------------------------------------------------------------------------

# Чтобы не пересоздавать графы и агентов на каждый запрос:
# - используется in-memory кеш на процесс,
# - каждый граф (по ключу persona) создаётся лениво,
# - при конкурентных запросах используется `asyncio.Lock`.

# Параметры кеша:
# 1) TTL (time-to-live):
#    - GRAPH_CACHE_TTL_S=0     -> кеш бессрочный (граф создаётся 1 раз на процесс)
#    - GRAPH_CACHE_TTL_S=600   -> граф пересоздаётся раз в 10 минут
# 2) Force reload:
#    - GRAPH_CACHE_FORCE_RELOAD=1 -> всегда пересоздавать граф (удобно для разработки)

# Важно:
# - кеш **процессный** (у каждой реплики свой),
# - это ожидаемое поведение в ASGI окружении (несколько воркеров = несколько кешей),
# - это нормальная практика: повышает производительность на горячем пути.

# -------------------------------------------------------------------------------
# ПУБЛИЧНЫЕ ФАБРИКИ ДЛЯ LANGGRAPH RUNTIME
# -------------------------------------------------------------------------------

# LangGraph runtime (через `langgraph.json` / `LANGSERVE_GRAPHS`) ожидает
# вида "module:function".

# Поэтому экспортируются функции:

#     make_graph_sofia
#     make_graph_anisa
#     ...

# Внутри они вызывают общую фабрику:

#     _make_graph("sofia")

# которая:
# - берёт настройки из `get_settings()`,
# - выбирает порт по имени,
# - забирает/создаёт граф из `_registry` (с TTL/force-reload),
# - возвращает `CompiledStateGraph`.

# Важно:
# - В текущей реализации список персон фиксирован в `get_settings().mcp_ports`.
# - Добавление новой персоны требует правки этого списка + добавления `make_graph_<name>()`
#   (позже можно заменить на env-driven auto-registration).

# -------------------------------------------------------------------------------
# СХЕМА: ВЫЗОВЫ И ДАННЫЕ (ГРУБО)
# -------------------------------------------------------------------------------

#                     ┌───────────────────────────┐
#                     │  LangGraph / LangServe    │
#                     │  runtime                  │
#                     └───────────────┬───────────┘
#                                     │ вызывает
#                                     ▼
#                           make_graph_<persona>()
#                                     │
#                                     ▼
#                               _make_graph(name)
#                                     │
#                                     ├─ get_settings()
#                                     │     ├─ читает env (порты, TTL, reload) [1 раз/процесс]
#                                     │     └─ кешируется на процесс (LRU)
#                                     │
#                                     └─ _registry.aget_or_create(name, factory)
#                                           │
#                                           ├─ (cache hit) вернуть граф
#                                           │
#                                           └─ (cache miss/expired/reload)
#                                                 ▼
#                                       create_agent_graph(port)
#                                                 │
#                                                 ├─ await init_resources()
#                                                 │
#                                                 ├─ agent = await create_agent_mcp(mcp_port=port)
#                                                 │
#                                                 └─ StateGraph: START → agent → END
#                                                     compile() -> CompiledStateGraph

# -------------------------------------------------------------------------------
# КАК ДЕБАЖИТЬ CACHE / TTL / FORCE-RELOAD (ОЖИДАЕМОЕ ПОВЕДЕНИЕ)
# -------------------------------------------------------------------------------

# - Первый вызов make_graph_* в воркере:
#   граф создаётся, init_resources() срабатывает, agent создаётся, граф кешируется.

# - Повторный вызов (TTL=0 и FORCE_RELOAD=0):
#   граф берётся из кеша, init_resources() и создание agent НЕ повторяются.

# - TTL истёк:
#   граф пересобирается (под lock), в кеш кладётся новая версия.

# - FORCE_RELOAD=1:
#   граф пересобирается на каждый запрос (дороже, но удобно для dev).

# Важно:
# - TTL/force-reload пересоздают граф,
#   но НЕ перечитывают env (get_settings() уже закеширован) — для этого нужен рестарт воркера.

# -------------------------------------------------------------------------------
# ДЛЯ НОВИЧКА (ОБЪЯСНЕНИЕ ПРОСТЫМИ СЛОВАМИ)
# -------------------------------------------------------------------------------

# Представь, что у тебя есть несколько “ботов” — София, Алена, Валентина.
# У каждого бота одинаковая логика общения (одинаковый граф),
# но каждый бот подключается к своему “набору инструментов” (MCP server) по своему порту.

# Этот файл делает три главные вещи:

# 1) **Готовит окружение**:
#    - вызывает init_runtime(), чтобы переменные окружения (.env) были загружены вовремя.

# 2) **Создаёт граф**:
#    - при первом запросе создаёт агента (agent) через create_agent_mcp,
#    - строит минимальный граф START → agent → END,
#    - компилирует граф.

# 3) **Кэширует граф**:
#    - чтобы не создавать агента заново на каждый запрос,
#    - хранит готовый граф в памяти процесса,
#    - при одновременных запросах защищает создание локом,
#    - при необходимости умеет пересоздавать (TTL) или всегда пересоздавать (force reload).
# """

# from __future__ import annotations

# # ------------------------------------------------------------------------------
# # Runtime init (ДОЛЖЕН быть выполнен до os.getenv и любых вычислений по env)
# # ------------------------------------------------------------------------------
# from .zena_runtime import init_runtime

# init_runtime()

# # ------------------------------------------------------------------------------
# # Imports
# # ------------------------------------------------------------------------------
# import asyncio
# import logging
# import os
# import time
# from dataclasses import dataclass
# from functools import lru_cache
# from typing import Awaitable, Callable, Dict, Mapping, Optional

# from langgraph.graph import END, START, StateGraph
# from langgraph.graph.state import CompiledStateGraph

# from .zena_create_agent import create_agent_mcp
# from .zena_resources import init_resources
# from .zena_state import Context, InputState, OutputState, State


# # ------------------------------------------------------------------------------
# # Logging
# # ------------------------------------------------------------------------------
# def _get_log_level(name: str, *, default: str = "INFO") -> int:
#     raw = os.getenv(name, default).strip().upper()
#     return getattr(logging, raw, logging.INFO)


# _LOGGER_NAME = os.getenv("GRAPH_CACHE_LOGGER_NAME", "zena.graph_cache").strip() or "zena.graph_cache"
# logger = logging.getLogger(_LOGGER_NAME)
# logger.setLevel(_get_log_level("GRAPH_CACHE_LOG_LEVEL", default="INFO"))


# # ------------------------------------------------------------------------------
# # Helpers
# # ------------------------------------------------------------------------------
# def _get_int_env(name: str, *, default: int | None = None) -> int:
#     """
#     Прочитать переменную окружения как int с понятными ошибками.

#     Поведение:
#     - если переменной нет и default не задан → RuntimeError
#     - если значение не int → RuntimeError
#     - если переменной нет, но default задан → default
#     """
#     raw = os.getenv(name)

#     if raw is None or raw == "":
#         if default is None:
#             raise RuntimeError(f"Missing required env var: {name}")
#         return default

#     try:
#         return int(raw)
#     except ValueError as e:
#         raise RuntimeError(f"Env var {name} must be int, got {raw!r}") from e


# def _get_float_env(name: str, *, default: float) -> float:
#     raw = os.getenv(name)
#     if raw is None or raw == "":
#         return default
#     try:
#         return float(raw)
#     except ValueError as e:
#         raise RuntimeError(f"Env var {name} must be float, got {raw!r}") from e


# def _get_bool_env(name: str, *, default: bool = False) -> bool:
#     raw = os.getenv(name)
#     if raw is None or raw == "":
#         return default
#     return raw.strip().lower() in {"1", "true", "yes", "on"}


# # ------------------------------------------------------------------------------
# # Settings (on-demand; устраняет import-time "залипания" конфигов)
# # ------------------------------------------------------------------------------
# @dataclass(frozen=True)
# class Settings:
#     """Runtime settings for graph factories."""
#     mcp_ports: Mapping[str, int]
#     graph_cache_ttl_s: float  # 0 -> never expire
#     graph_cache_force_reload: bool


# @lru_cache(maxsize=1)
# def get_settings() -> Settings:
#     ports: dict[str, int] = {
#         "sofia": _get_int_env("MCP_PORT_SOFIA", default=5002),
#         "anisa": _get_int_env("MCP_PORT_ANISA", default=5005),
#         "annitta": _get_int_env("MCP_PORT_ANNITTA", default=5006),
#         "anastasia": _get_int_env("MCP_PORT_ANASTASIA", default=5007),
#         "alena": _get_int_env("MCP_PORT_ALENA", default=5020),
#         "valentina": _get_int_env("MCP_PORT_VALENTINA", default=5021),
#         "marina": _get_int_env("MCP_PORT_MARINA", default=5024),
#     }

#     for k, p in ports.items():
#         if not (1 <= p <= 65535):
#             raise RuntimeError(f"Bad MCP port for {k!r}: {p}. Must be 1..65535")

#     ttl_s = _get_float_env("GRAPH_CACHE_TTL_S", default=0.0)
#     force_reload = _get_bool_env("GRAPH_CACHE_FORCE_RELOAD", default=False)

#     if ttl_s < 0:
#         raise RuntimeError("GRAPH_CACHE_TTL_S must be >= 0")

#     return Settings(
#         mcp_ports=ports,
#         graph_cache_ttl_s=ttl_s,
#         graph_cache_force_reload=force_reload,
#     )


# # ------------------------------------------------------------------------------
# # Graph factory
# # ------------------------------------------------------------------------------
# async def create_agent_graph(port: int) -> CompiledStateGraph:
#     """
#     Универсальная фабрика LangGraph-графа.

#     Шаги:
#     1) Fail-fast инициализация shared ресурсов
#     2) Создание agent-узла (MCP на заданном порту)
#     3) Построение графа START → agent → END
#     4) Компиляция графа
#     """
#     await init_resources()

#     agent = await create_agent_mcp(mcp_port=port)

#     workflow = StateGraph(
#         state_schema=State,
#         input_schema=InputState,
#         output_schema=OutputState,
#         context_schema=Context,
#     )
#     workflow.add_node("agent", agent)
#     workflow.add_edge(START, "agent")
#     workflow.add_edge("agent", END)

#     return workflow.compile()


# # ------------------------------------------------------------------------------
# # Graph registry (per-process lazy cache)
# # ------------------------------------------------------------------------------
# @dataclass
# class _GraphEntry:
#     """Запись кеша одного графа."""
#     lock: asyncio.Lock
#     graph: Optional[CompiledStateGraph] = None
#     initialized_at: Optional[float] = None


# class _GraphRegistry:
#     """
#     In-memory реестр графов по ключу (sofia, anisa, ...).

#     Обеспечивает:
#     - ленивое создание,
#     - защиту от конкурентного создания,
#     - кеширование на процесс,
#     - TTL и принудительный reload,
#     - логирование cache hit/miss/expired/force_reload.
#     """

#     def __init__(self) -> None:
#         self._items: Dict[str, _GraphEntry] = {}

#     def _entry(self, key: str) -> _GraphEntry:
#         if key not in self._items:
#             self._items[key] = _GraphEntry(lock=asyncio.Lock())
#         return self._items[key]

#     @staticmethod
#     def _age_s(entry: _GraphEntry) -> Optional[float]:
#         if entry.initialized_at is None:
#             return None
#         return time.time() - entry.initialized_at

#     @staticmethod
#     def _is_fresh(entry: _GraphEntry, ttl_s: float) -> bool:
#         if ttl_s <= 0:
#             return True
#         if entry.initialized_at is None:
#             return False
#         return (time.time() - entry.initialized_at) <= ttl_s

#     async def aget_or_create(
#         self,
#         key: str,
#         factory: Callable[[], Awaitable[CompiledStateGraph]],
#         *,
#         ttl_s: float = 0.0,
#         force_reload: bool = False,
#         port: Optional[int] = None,
#     ) -> CompiledStateGraph:
#         """
#         Получить граф из кеша или создать.

#         Логи:
#         - cache_status=hit        (взяли из кеша)
#         - cache_status=miss       (кеша не было)
#         - cache_status=expired    (TTL истёк)
#         - cache_status=force_reload (принудительная пересборка)
#         """
#         entry = self._entry(key)
#         age = self._age_s(entry)

#         # Fast-path: cache hit
#         if entry.graph is not None and not force_reload and self._is_fresh(entry, ttl_s):
#             logger.info(
#                 "graph_cache event=graph_get key=%s cache_status=hit ttl_s=%s force_reload=%s age_s=%s mcp_port=%s",
#                 key,
#                 ttl_s,
#                 force_reload,
#                 None if age is None else round(age, 3),
#                 port,
#             )
#             return entry.graph

#         # Определяем причину пересоздания для лога (до lock)
#         if force_reload:
#             status = "force_reload"
#         else:
#             status = "miss" if entry.graph is None else "expired"

#         async with entry.lock:
#             # Double-check under lock
#             age = self._age_s(entry)
#             if entry.graph is not None and not force_reload and self._is_fresh(entry, ttl_s):
#                 logger.info(
#                     "graph_cache event=graph_get key=%s cache_status=hit ttl_s=%s force_reload=%s age_s=%s mcp_port=%s",
#                     key,
#                     ttl_s,
#                     force_reload,
#                     None if age is None else round(age, 3),
#                     port,
#                 )
#                 return entry.graph

#             logger.info(
#                 "graph_cache event=graph_build key=%s cache_status=%s ttl_s=%s force_reload=%s age_s=%s mcp_port=%s",
#                 key,
#                 status,
#                 ttl_s,
#                 force_reload,
#                 None if age is None else round(age, 3),
#                 port,
#             )

#             g = await factory()
#             entry.graph = g
#             entry.initialized_at = time.time()

#             logger.info(
#                 "graph_cache event=graph_ready key=%s ttl_s=%s force_reload=%s mcp_port=%s",
#                 key,
#                 ttl_s,
#                 force_reload,
#                 port,
#             )

#             return g


# _registry = _GraphRegistry()


# # ------------------------------------------------------------------------------
# # Public graph factories (used by LangGraph CLI / runtime)
# # ------------------------------------------------------------------------------
# async def _make_graph(name: str) -> CompiledStateGraph:
#     s = get_settings()
#     try:
#         port = s.mcp_ports[name]
#     except KeyError as e:
#         raise RuntimeError(
#             f"Unknown graph key {name!r}. Available: {sorted(s.mcp_ports.keys())}"
#         ) from e

#     return await _registry.aget_or_create(
#         name,
#         lambda: create_agent_graph(port),
#         ttl_s=s.graph_cache_ttl_s,
#         force_reload=s.graph_cache_force_reload,
#         port=port,
#     )


# async def make_graph_sofia() -> CompiledStateGraph:
#     return await _make_graph("sofia")


# async def make_graph_anisa() -> CompiledStateGraph:
#     return await _make_graph("anisa")


# async def make_graph_annitta() -> CompiledStateGraph:
#     return await _make_graph("annitta")


# async def make_graph_anastasia() -> CompiledStateGraph:
#     return await _make_graph("anastasia")


# async def make_graph_alena() -> CompiledStateGraph:
#     return await _make_graph("alena")


# async def make_graph_valentina() -> CompiledStateGraph:
#     return await _make_graph("valentina")


# async def make_graph_marina() -> CompiledStateGraph:
#     return await _make_graph("marina")
