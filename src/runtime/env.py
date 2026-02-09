# src/runtime/env.py
"""
Модуль: runtime.env
===================

Назначение
----------

Этот модуль содержит **унифицированные helper-функции** для чтения переменных
окружения (`os.getenv`) с валидацией и понятными ошибками.

Зачем выносить helpers отдельно:
- чтобы не дублировать однотипную логику по проекту,
- чтобы ошибки конфигурации были **одинаково оформлены** и легко диагностировались,
- чтобы любые изменения политики чтения env (например, "true"/"false" значения)
  делались в одном месте.

Важно
-----

- Этот модуль **НЕ вызывает** `init_runtime()` и **НЕ загружает** `.env`.
  Предполагается, что загрузка окружения выполняется в entrypoint (например, в
  `zena_create_graph.py` через `init_runtime()`), до того как helpers начнут
  использоваться.

Содержимое
----------

- `get_int_env(name, default=...)`    -> int
- `get_float_env(name, default=...)`  -> float
- `get_bool_env(name, default=...)`   -> bool
"""

from __future__ import annotations

import os


def get_int_env(name: str, *, default: int | None = None) -> int:
    """
    Прочитать переменную окружения как int с понятными ошибками.

    Поведение:
    - если переменной нет и default не задан → RuntimeError
    - если значение не int → RuntimeError
    - если переменной нет, но default задан → default
    """
    raw = os.getenv(name)

    if raw is None or raw == "":
        if default is None:
            raise RuntimeError(f"Missing required env var: {name}")
        return default

    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {name} must be int, got {raw!r}") from e


def get_float_env(name: str, *, default: float) -> float:
    """
    Прочитать переменную окружения как float с понятными ошибками.

    Поведение:
    - если переменной нет или пустая → default
    - если значение не float → RuntimeError
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {name} must be float, got {raw!r}") from e


def get_bool_env(name: str, *, default: bool = False) -> bool:
    """
    Прочитать переменную окружения как bool.

    True значения: "1", "true", "yes", "on" (case-insensitive).
    Любое другое непустое значение трактуется как False.

    Поведение:
    - если переменной нет или пустая → default
    - иначе → bool по правилу выше
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
