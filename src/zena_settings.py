"""Pydantic-settings wrapper for env-driven configuration.

Currently covers MCP port assignments for the 8 agents. Fails fast
with pydantic.ValidationError at startup if any required var is missing
or non-numeric, instead of failing silently at first tool call with
'http://host:None/sse'.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=None,  # .env is already loaded in zena_common / zena_postgres
        extra="ignore",
        case_sensitive=False,
    )

    mcp_port_sofia: int
    mcp_port_anisa: int
    mcp_port_annitta: int
    mcp_port_anastasia: int
    mcp_port_alena: int
    mcp_port_valentina: int
    mcp_port_marina: int
    mcp_port_egoistka: int


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the validated Settings singleton, constructing it on first call.

    Import of this module is side-effect-free; validation happens on first
    call to get_settings(), which in the normal service lifecycle is at
    startup (inside zena_create_graph).
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
