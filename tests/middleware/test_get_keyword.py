"""Tests for GetKeyWordMiddleware — bug #1 (return shape)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.zena_middleware_before_agent import GetKeyWordMiddleware


@pytest.mark.asyncio
async def test_returns_data_under_nested_key(agent_state_factory, runtime_factory):
    """Returned dict must place updates under 'data', not at top level."""
    state = agent_state_factory(data={"channel_id": 1})
    runtime = runtime_factory()
    mw = GetKeyWordMiddleware()

    with patch(
        "src.zena_middleware_before_agent.fetch_key_words",
        new=AsyncMock(return_value=[{"id": 7, "name": "promo-service"}]),
    ):
        result = await mw.abefore_agent(state, runtime)

    assert result is not None
    assert "data" in result, "update must be nested under 'data'"
    assert result["data"]["items_search"] == [{"id": 7, "name": "promo-service"}]
    assert result["data"]["dialog_state"] == "promo"
    assert "items_search" not in result, "must NOT leak to top-level state"
    assert "dialog_state" not in result, "must NOT leak to top-level state"


@pytest.mark.asyncio
async def test_does_not_mutate_input_state(agent_state_factory, runtime_factory):
    """Input state['data'] must be unchanged after middleware runs."""
    original_data = {"channel_id": 1, "existing_key": "keep"}
    state = agent_state_factory(data=original_data)
    runtime = runtime_factory()
    mw = GetKeyWordMiddleware()

    with patch(
        "src.zena_middleware_before_agent.fetch_key_words",
        new=AsyncMock(return_value=[{"id": 7}]),
    ):
        await mw.abefore_agent(state, runtime)

    assert "items_search" not in original_data, "input dict was mutated"
    assert "dialog_state" not in original_data, "input dict was mutated"
    assert original_data == {"channel_id": 1, "existing_key": "keep"}


@pytest.mark.asyncio
async def test_returns_none_when_no_keywords_match(agent_state_factory, runtime_factory):
    """If fetch_key_words returns empty, middleware returns None (no update)."""
    state = agent_state_factory(data={"channel_id": 1})
    runtime = runtime_factory()
    mw = GetKeyWordMiddleware()

    with patch(
        "src.zena_middleware_before_agent.fetch_key_words",
        new=AsyncMock(return_value=[]),
    ):
        result = await mw.abefore_agent(state, runtime)

    assert result is None
