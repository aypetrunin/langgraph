# src/runtime/logging.py
"""
Модуль: runtime.logging
=======================

Назначение
----------

Этот модуль отвечает за **настройку логирования runtime-компонентов**,
в первую очередь — кеша LangGraph-графов.

Зачем вынесен отдельно:
- чтобы entrypoint (`zena_create_graph.py`) не содержал инфраструктурный шум,
- чтобы централизовать работу с env-переменными логирования,
- чтобы при необходимости легко переиспользовать или расширить логирование
  (например, добавить structured logging, JSON, trace_id и т.п.).

Управление через env
--------------------

- GRAPH_CACHE_LOGGER_NAME
    Имя логгера.
    По умолчанию: "zena.graph_cache"

- GRAPH_CACHE_LOG_LEVEL
    Уровень логирования.
    Допустимые значения: DEBUG, INFO, WARNING, ERROR, CRITICAL
    По умолчанию: INFO

Пример:
    GRAPH_CACHE_LOG_LEVEL=DEBUG
    GRAPH_CACHE_LOGGER_NAME=zena.graph_cache

Важно
-----

- Модуль **не настраивает handlers** (StreamHandler, FileHandler и т.д.).
  Предполагается, что:
  - либо handlers настраиваются глобально (logging.basicConfig),
  - либо этим занимается ASGI runtime / контейнер / приложение.

- Если логгер не используется (не передан в GraphRegistry),
  логирование кеша будет полностью отключено.
"""

from __future__ import annotations

import logging
import os


def _get_log_level(name: str, *, default: str = "INFO") -> int:
    raw = os.getenv(name, default).strip().upper()
    return getattr(logging, raw, logging.INFO)


def get_graph_cache_logger() -> logging.Logger:
    """
    Создать и вернуть логгер для кеша графов.

    Логгер:
    - имя берётся из GRAPH_CACHE_LOGGER_NAME,
    - уровень — из GRAPH_CACHE_LOG_LEVEL.
    """
    logger_name = os.getenv("GRAPH_CACHE_LOGGER_NAME", "zena.graph_cache").strip()
    if not logger_name:
        logger_name = "zena.graph_cache"

    logger = logging.getLogger(logger_name)
    logger.setLevel(_get_log_level("GRAPH_CACHE_LOG_LEVEL", default="INFO"))
    return logger
