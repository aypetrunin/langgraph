# src/runtime/settings.py
"""
Модуль: runtime.settings
========================

Назначение
----------

Этот модуль отвечает за сбор и валидацию **настроек runtime**, используемых при
создании LangGraph-графов и управлении их кешем.

Почему вынесли Settings отдельно:
- чтобы в entrypoint (например, `zena_create_graph.py`) не разрасталась логика
  чтения env и валидации,
- чтобы обеспечить единый источник правды по конфигурации,
- чтобы упростить будущую реструктуризацию (например, переход на env-driven
  список персон, автогенерацию фабрик и т.д.).

Ключевые свойства
-----------------

- `get_settings()` кешируется (LRU, maxsize=1) и читается **один раз на процесс**.
  Это ожидаемое поведение в ASGI/worker окружениях:
  - изменения env “на лету” не подхватываются без рестарта воркера,
  - TTL/force-reload пересоздают граф, но **не перечитывают env**.

Что входит в Settings
---------------------

- `mcp_ports`: Mapping[str, int]
    Мапа { persona_name -> MCP port }.
    В текущей реализации список персон фиксирован в коде.

- `graph_cache_ttl_s`: float
    TTL процессного кеша графов:
    - 0  -> бессрочно (граф создается 1 раз на процесс)
    - >0 -> граф пересоздаётся после истечения TTL

- `graph_cache_force_reload`: bool
    Принудительное пересоздание графа при каждом запросе (удобно для dev/studio).

Env-переменные
--------------

- MCP_PORT_SOFIA, MCP_PORT_ANISA, ...
- GRAPH_CACHE_TTL_S
- GRAPH_CACHE_FORCE_RELOAD
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

from .env import get_bool_env, get_float_env, get_int_env


@dataclass(frozen=True)
class Settings:
    """Runtime settings for graph factories."""
    mcp_ports: Mapping[str, int]
    graph_cache_ttl_s: float  # 0 -> never expire
    graph_cache_force_reload: bool


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    ports: dict[str, int] = {
        "sofia": get_int_env("MCP_PORT_SOFIA", default=5002),
        "anisa": get_int_env("MCP_PORT_ANISA", default=5005),
        "annitta": get_int_env("MCP_PORT_ANNITTA", default=5006),
        "anastasia": get_int_env("MCP_PORT_ANASTASIA", default=5007),
        "alena": get_int_env("MCP_PORT_ALENA", default=5020),
        "valentina": get_int_env("MCP_PORT_VALENTINA", default=5021),
        "marina": get_int_env("MCP_PORT_MARINA", default=5024),
    }

    for k, p in ports.items():
        if not (1 <= p <= 65535):
            raise RuntimeError(f"Bad MCP port for {k!r}: {p}. Must be 1..65535")

    ttl_s = get_float_env("GRAPH_CACHE_TTL_S", default=0.0)
    if ttl_s < 0:
        raise RuntimeError("GRAPH_CACHE_TTL_S must be >= 0")

    force_reload = get_bool_env("GRAPH_CACHE_FORCE_RELOAD", default=False)

    return Settings(
        mcp_ports=ports,
        graph_cache_ttl_s=ttl_s,
        graph_cache_force_reload=force_reload,
    )
