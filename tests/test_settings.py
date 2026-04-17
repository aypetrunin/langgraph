"""Tests for zena_settings — bug #4 (MCP port env validation)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _env_for_all_ports() -> dict[str, str]:
    return {
        "MCP_PORT_SOFIA": "5002",
        "MCP_PORT_ANISA": "5005",
        "MCP_PORT_ANNITTA": "5006",
        "MCP_PORT_ANASTASIA": "5007",
        "MCP_PORT_ALENA": "5020",
        "MCP_PORT_VALENTINA": "5021",
        "MCP_PORT_MARINA": "5024",
        "MCP_PORT_EGOISTKA": "5017",
    }


def test_settings_loads_from_env(monkeypatch):
    for k, v in _env_for_all_ports().items():
        monkeypatch.setenv(k, v)
    # Ensure .env file fallback does not interfere
    monkeypatch.setenv("IS_DOCKER", "1")

    from src.zena_settings import Settings

    s = Settings()
    assert s.mcp_port_sofia == 5002
    assert s.mcp_port_egoistka == 5017
    assert isinstance(s.mcp_port_alena, int)


def test_settings_fails_fast_when_var_missing(monkeypatch):
    env = _env_for_all_ports()
    env.pop("MCP_PORT_SOFIA")  # simulate missing var
    for k in list(env):
        monkeypatch.setenv(k, env[k])
    monkeypatch.delenv("MCP_PORT_SOFIA", raising=False)
    monkeypatch.setenv("IS_DOCKER", "1")

    from src.zena_settings import Settings

    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "mcp_port_sofia" in str(exc_info.value).lower()


def test_settings_fails_fast_when_var_not_int(monkeypatch):
    for k, v in _env_for_all_ports().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MCP_PORT_SOFIA", "not-a-number")
    monkeypatch.setenv("IS_DOCKER", "1")

    from src.zena_settings import Settings

    with pytest.raises(ValidationError):
        Settings()


def test_create_graph_module_exposes_all_ports(monkeypatch):
    """zena_create_graph will consume get_settings() — verify all 8 attrs are int."""
    for k, v in _env_for_all_ports().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("IS_DOCKER", "1")

    from src.zena_settings import get_settings

    s = get_settings()
    expected_attrs = [
        "mcp_port_sofia", "mcp_port_anisa", "mcp_port_annitta",
        "mcp_port_anastasia", "mcp_port_alena", "mcp_port_valentina",
        "mcp_port_marina", "mcp_port_egoistka",
    ]
    for attr in expected_attrs:
        assert isinstance(getattr(s, attr), int)
