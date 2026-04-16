# langgraph — Agent Orchestration Service

## What This Service Does

LangGraph-based agent orchestration. Builds execution graphs that process client requests, manages conversation state/memory, and connects to MCP tools via mcpserver.

## Project Structure

```
langgraph/
├── langgraph.json               # LangGraph config
├── src/
│   ├── zena_agent.py            # Main agent definition
│   ├── zena_create_agent.py     # Agent factory
│   ├── zena_create_graph.py     # Graph construction
│   ├── zena_agent_node.py       # Agent graph node
│   ├── zena_state.py            # Graph state schema
│   ├── zena_memory.py           # Conversation memory
│   ├── zena_common.py           # Shared utilities
│   ├── zena_requests.py         # External request helpers
│   ├── zena_httpservice.py      # HTTP client
│   ├── zena_postgres.py         # PostgreSQL queries
│   ├── zena_tokens.py           # Token counting
│   ├── zena_google_doc.py       # Google Docs integration
│   ├── zena_request_masters_cache.py  # Masters cache
│   ├── zena_redialog_agent.py   # Re-dialog agent
│   ├── zena_redialog_graph.py   # Re-dialog graph
│   ├── deepagent.py             # Deep agent logic
│   ├── zena_middleware_*.py     # Middleware (before/after model/agent, wrap)
│   └── zena_test_mcp_server.py  # MCP server test
```

## Common Commands

```bash
# Run in dev mode
uv run langgraph dev --port 2025

# Build docker image
langgraph build -c langgraph.json -t zena-agent:latest

# Install deps
uv sync

# Lint & format
uv run ruff check src/
uv run ruff format src/
uv run mypy src/
```

## Code Style

- ruff (line-length=88), mypy strict with pydantic plugin
- Google-style docstrings
- No prints (use structlog)
- All files prefixed with `zena_` by convention
