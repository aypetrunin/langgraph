import asyncio
import logging
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import BaseTool
from typing import List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def _get_tools(mcp_port: int) -> List[BaseTool]:
    """Получение инструментов из MCP-сервера по выбранному порту."""
    # Пробуем оба адреса
    for host in ["127.0.0.1", "172.17.0.1", "localhost"]:
        url = f"http://{host}:{mcp_port}/sse"
        logger.info(f"Пробуем подключиться к {url}")
        try:
            client = MultiServerMCPClient({
                "company": {
                    "transport": "sse",
                    "url": url,
                }
            })
            tools = await client.get_tools()
            logger.info(f"✅ УСПЕХ на {host}:{mcp_port}")
            return tools
        except Exception as e:
            logger.warning(f"❌ {host}:{mcp_port} недоступен: {e}")
            continue
    raise Exception(f"Все адреса для порта {mcp_port} недоступны")

async def main():
    mcp_ports = [5001, 5002, 5005, 5006, 5007, 5020]
    for port in mcp_ports:
        try:
            tools = await _get_tools(port)
            tools_name = [tool.name for tool in tools]
            logger.info(f"create_agent_mcp tools ({port}): {tools_name}")
        except Exception as e:
            logger.error(f"❌ Порт {port} полностью недоступен: {e}")

if __name__ == "__main__":
    asyncio.run(main())

# cd /home/copilot_superuser/petrunin/zena/langgraph
# uv run python -m src.zena_test_mcp_server