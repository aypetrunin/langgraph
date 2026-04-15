"""Диагностическая утилита для проверки подключения к MCP-серверам.

Перебирает хосты (127.0.0.1, 172.17.0.1, localhost) для каждого порта
и выводит список доступных инструментов.

Запуск: uv run python -m src.zena_test_mcp_server
"""

import asyncio
from typing import List

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.zena_logging import get_logger, setup_logging

setup_logging()
logger = get_logger()

async def _get_tools(mcp_port: int) -> List[BaseTool]:
    """Получение инструментов из MCP-сервера по выбранному порту."""
    # Пробуем оба адреса
    for host in ["127.0.0.1", "172.17.0.1", "localhost"]:
        url = f"http://{host}:{mcp_port}/sse"
        logger.info("mcp.connecting", url=url)
        try:
            client = MultiServerMCPClient({
                "company": {
                    "transport": "sse",
                    "url": url,
                }
            })
            tools = await client.get_tools()
            logger.info("mcp.connected", host=host, port=mcp_port)
            return tools
        except Exception as e:
            logger.warning("mcp.unavailable", host=host, port=mcp_port, error=str(e))
            continue
    raise Exception(f"Все адреса для порта {mcp_port} недоступны")

async def main() -> None:
    """Проверяет доступность инструментов на всех MCP-портах."""
    mcp_ports = [5001, 5002, 5005, 5006, 5007, 5020]
    for port in mcp_ports:
        try:
            tools = await _get_tools(port)
            tools_name = [tool.name for tool in tools]
            logger.info("mcp.tools_loaded", port=port, tools=tools_name)
        except Exception as e:
            logger.error("mcp.port_unavailable", port=port, error=str(e))

if __name__ == "__main__":
    asyncio.run(main())

# cd /home/copilot_superuser/petrunin/zena/langgraph
# uv run python -m src.zena_test_mcp_server