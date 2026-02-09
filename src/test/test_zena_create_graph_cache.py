# cd /home/copilot_superuser/petrunin/zena/langgraph/src/test
# uv run pytest -q

import asyncio
import importlib
import sys
from pathlib import Path
import types

import pytest

# --- make 'src' a package for tests (so relative imports work) ---
THIS_DIR = Path(__file__).resolve().parent          # .../langgraph/src/test
SRC_DIR = THIS_DIR.parent                           # .../langgraph/src

if str(SRC_DIR.parent) not in sys.path:            # .../langgraph
    sys.path.insert(0, str(SRC_DIR.parent))

if "src" not in sys.modules:
    pkg = types.ModuleType("src")
    pkg.__path__ = [str(SRC_DIR)]
    sys.modules["src"] = pkg

m = importlib.import_module("src.zena_create_graph")


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    from src.graphs.registry import GraphRegistry
    monkeypatch.setattr(m, "_registry", GraphRegistry())
    yield


def _make_settings(*, ttl: float, force: bool):
    from src.runtime.settings import Settings
    return Settings(
        mcp_ports={"sofia": 5002},
        graph_cache_ttl_s=ttl,
        graph_cache_force_reload=force,
    )



async def _fake_create_agent_graph(_port: int):
    from langgraph.graph import StateGraph, START, END

    wf = StateGraph(dict)

    def agent(state: dict) -> dict:
        return state

    wf.add_node("agent", agent)
    wf.add_edge(START, "agent")
    wf.add_edge("agent", END)

    return wf.compile()


@pytest.mark.asyncio
async def test_make_graph_sofia_returns_compiled_graph(monkeypatch):
    monkeypatch.setattr(m, "get_settings", lambda: _make_settings(ttl=0.0, force=False))
    monkeypatch.setattr(m, "create_agent_graph", _fake_create_agent_graph)

    g = await m.make_graph_sofia()

    from langgraph.graph.state import CompiledStateGraph

    assert isinstance(g, CompiledStateGraph)


@pytest.mark.asyncio
async def test_ttl_0_returns_same_object(monkeypatch):
    monkeypatch.setattr(m, "get_settings", lambda: _make_settings(ttl=0.0, force=False))
    monkeypatch.setattr(m, "create_agent_graph", _fake_create_agent_graph)

    g1 = await m.make_graph_sofia()
    g2 = await m.make_graph_sofia()

    assert g1 is g2


@pytest.mark.asyncio
async def test_force_reload_returns_new_object(monkeypatch):
    monkeypatch.setattr(m, "get_settings", lambda: _make_settings(ttl=0.0, force=True))
    monkeypatch.setattr(m, "create_agent_graph", _fake_create_agent_graph)

    g1 = await m.make_graph_sofia()
    g2 = await m.make_graph_sofia()

    assert g1 is not g2


@pytest.mark.asyncio
async def test_ttl_1_expires_after_sleep(monkeypatch):
    monkeypatch.setattr(m, "get_settings", lambda: _make_settings(ttl=1.0, force=False))
    monkeypatch.setattr(m, "create_agent_graph", _fake_create_agent_graph)

    g1 = await m.make_graph_sofia()
    await asyncio.sleep(1.05)
    g2 = await m.make_graph_sofia()

    assert g1 is not g2
