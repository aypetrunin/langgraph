# zena_models.py
"""Фабрика LLM-моделей + общий httpx.AsyncClient (инициализация/закрытие)."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx
from langchain.chat_models import init_chat_model

logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


@dataclass(frozen=True)
class Models:
    model_4o_mini: object
    model_4o_mini_reserv: object
    model_4o: object


class _ModelsRegistry:
    """
    Потокобезопасный (для asyncio) singleton.
    Гарантирует:
    - модели создаются один раз на процесс
    - есть общий httpx.AsyncClient
    - close() закрывает клиент
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._models: Optional[Models] = None
        self._http: Optional[httpx.AsyncClient] = None

    async def init(self) -> Models:
        if self._models is not None:
            return self._models

        async with self._lock:
            if self._models is not None:
                return self._models

            openai_proxy = os.getenv("OPENAI_PROXY_URL")  # proxy может быть пустым
            openai_model_4o_mini = _require_env("OPENAI_MODEL_4O_MINI")
            openai_model_4o = _require_env("OPENAI_MODEL_4O")
            openai_api_key = _require_env("OPENAI_API_KEY")
            openai_api_key_reserv = _require_env("OPENAI_API_KEY_RESERV")

            # Один общий клиент на процесс (важно закрывать!)
            # При желании можно добавить limits=..., http2=True, transport=...
            self._http = httpx.AsyncClient(
                proxy=openai_proxy if openai_proxy else None,
                timeout=httpx.Timeout(60.0),
            )

            self._models = Models(
                model_4o_mini=init_chat_model(
                    model=openai_model_4o_mini,
                    api_key=openai_api_key,
                    temperature=0,
                    http_async_client=self._http,
                ),
                model_4o_mini_reserv=init_chat_model(
                    model=openai_model_4o_mini,
                    api_key=openai_api_key_reserv,
                    temperature=0,
                    http_async_client=self._http,
                ),
                model_4o=init_chat_model(
                    model=openai_model_4o,
                    api_key=openai_api_key,
                    temperature=0,
                    http_async_client=self._http,
                ),
            )

            logger.info("LLM models initialized: 4o_mini=%s, 4o=%s", openai_model_4o_mini, openai_model_4o)
            return self._models

    def get(self) -> Models:
        if self._models is None:
            raise RuntimeError("Models are not initialized. Call await init_models() first.")
        return self._models

    async def close(self) -> None:
        async with self._lock:
            if self._http is not None:
                try:
                    await self._http.aclose()
                finally:
                    self._http = None
            self._models = None
            logger.info("LLM models closed")


_registry = _ModelsRegistry()


async def init_models() -> Models:
    return await _registry.init()


def get_models() -> Models:
    return _registry.get()


async def close_models() -> None:
    await _registry.close()
