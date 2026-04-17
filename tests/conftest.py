"""Pytest fixtures for langgraph unit tests."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import HumanMessage


@pytest.fixture
def agent_state_factory() -> Callable[..., dict[str, Any]]:
    """Factory for State dicts used in middleware tests.

    Returns a callable that produces a minimal dict matching the keys
    middleware actually read from state: `messages`, `data`, `tokens`.
    """

    def _make(
        *,
        messages: list[Any] | None = None,
        data: dict[str, Any] | None = None,
        tokens: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "messages": messages or [HumanMessage(content="test")],
            "data": data or {},
            "tokens": tokens or {},
        }

    return _make


@pytest.fixture
def runtime_factory() -> Callable[..., SimpleNamespace]:
    """Factory for a fake Runtime[Context].

    Middleware only read `runtime.context` (a dict) and occasionally
    `runtime.store`. A SimpleNamespace is enough.
    """

    def _make(context: dict[str, Any] | None = None) -> SimpleNamespace:
        return SimpleNamespace(context=context or {}, store=None)

    return _make
