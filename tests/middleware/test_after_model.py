"""Tests for GetCRMGOOnboardStage — bug #7 (immutable update)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from src.zena_middleware_after_model import GetCRMGOOnboardStage


def _state_for_onboarding(stage: int, *, status: bool = False, mcp_port: int = 5020):
    return {
        "messages": [AIMessage(content="hi")],
        "data": {
            "mcp_port": mcp_port,
            "onboarding": {
                "onboarding_status": status,
                "onboarding_stage": stage,
            },
        },
        "tokens": {},
    }


@pytest.mark.asyncio
async def test_does_not_mutate_input_onboarding(runtime_factory):
    """Middleware must not mutate the nested 'onboarding' dict in input state."""
    state = _state_for_onboarding(stage=2)
    original_onboarding = state["data"]["onboarding"]
    original_stage = original_onboarding["onboarding_stage"]

    mw = GetCRMGOOnboardStage()
    result = await mw.aafter_model(state, runtime_factory())

    # Input must be untouched
    assert state["data"]["onboarding"]["onboarding_stage"] == original_stage
    assert state["data"]["onboarding"] is original_onboarding  # same ref, not replaced

    # Returned update has the incremented value under a NEW dict
    assert result is not None
    assert result["data"]["onboarding"]["onboarding_stage"] == original_stage + 1
    assert result["data"]["onboarding"] is not original_onboarding


@pytest.mark.asyncio
async def test_noop_when_not_port_5020(runtime_factory):
    """Non-5020 ports skip onboarding updates."""
    state = _state_for_onboarding(stage=2, mcp_port=5002)
    mw = GetCRMGOOnboardStage()
    assert await mw.aafter_model(state, runtime_factory()) is None


@pytest.mark.asyncio
async def test_noop_when_already_onboarded(runtime_factory):
    """status=True means user already finished onboarding — do not increment."""
    state = _state_for_onboarding(stage=2, status=True)
    mw = GetCRMGOOnboardStage()
    assert await mw.aafter_model(state, runtime_factory()) is None


@pytest.mark.asyncio
async def test_caps_at_stage_6(runtime_factory):
    """Stage stops incrementing at 6 (< 6 guard in source)."""
    state = _state_for_onboarding(stage=6)
    mw = GetCRMGOOnboardStage()
    result = await mw.aafter_model(state, runtime_factory())
    # Still returns an update dict (current code does), but stage unchanged
    assert result is not None
    assert result["data"]["onboarding"]["onboarding_stage"] == 6
