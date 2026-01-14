from typing import Any, Mapping, Optional, cast

from langgraph.runtime import Runtime
from langchain.agents.middleware import AgentMiddleware

from .zena_common import logger
from .zena_state import State, Context, RESET
from .zena_common import _content_to_text, _func_name
from .zena_httpservice import sent_message_to_history


class SaveResponceAgent(AgentMiddleware):
    
    async def aafter_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Async logic to run after the model is called."""

        logger.info("===after_agent===SaveResponceAgent===")
        
        try:
            data = state.get('data', {})
            text = _content_to_text(state['messages'][-1].content)
            ctx = runtime.context
            # Здесь решается проблема откуда запущен агент из приложения или из Studio
            # Для Studio определена только переменная - user_companychat
            user_id = ctx.get('_user_id') or int(data.get('user_id'))
            access_token = ctx.get('_access_token') or data.get('session_id','').split('-', 1)[1]
            reply_to_history_id = ctx.get('_reply_to_history_id', 11050) if text != 'Память очищена' else 11050

            payload = {
                "user_id": user_id,
                "text": text,
                "access_token": access_token,
                "user_companychat": state.get('user_companychat'),
                "reply_to_history_id": reply_to_history_id,
                "tools": state.get('tools_result', []),
                "tools_args": None,
                "tools_result": None,
                "tokens": state.get('tokens',{}),
                "prompt_system": data.get('prompt_system', ''),
                "dialog_state": data.get('dialog_state_in', ''),
                "dialog_state_new": data.get('dialog_state', ''),
                "template_prompt_system": data.get('template_prompt_system', ''),
            }

            # logger.info(f"payload: {payload}")

            responce = await sent_message_to_history(**payload)
            
            if responce.get('status', 'not')=='ok':
                logger.info(f"Ответ агента сохранен в postgres.")
            else:
                logger.error(f"Ошибка сохранения ответа агента в postgres.")

            return None
        
        except Exception as err:
            logger.exception(f"SaveResponceAgent: {err}")
            return None
        # content = _content_to_text(state['messages'][-1].content)
        # responce_mem = await memory.add(
        #     messages=[
        #         {"role": "assistant", "content": content},
        #      ],
        #     agent_id='assistent',
        #     run_id="test",
        #     infer=True,
        #     version="v2",
        #     output_format="v1.1"
        # )
        # logger.info(f"\nresponce_mem: {responce_mem}")


class ResetData(AgentMiddleware):
    """Сброс данных после ответа агента."""

    async def aafter_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> Optional[dict[str, Any]]:
        logger.info("===after_agent===ResetData===")

        return {
            "tools_args": RESET,
            "tools_name": RESET,
            "tools_result": RESET,
            "tokens": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        }