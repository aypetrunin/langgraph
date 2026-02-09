# src/zena_redialog_agent.py
"""
Агент реанимации диалога (Redialog).

Важно:
- НЕ создаём агента на import-time.
- Модели берём через zena_models.get_models(), но только после init_resources(),
  чтобы init_models() и http clients были подняты корректно.
- Возвращаем готового агента через async-фабрику с кешированием на процесс.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentState
from langgraph.graph.state import CompiledStateGraph

from .zena_resources import init_resources
from .zena_models import get_models


_SYSTEM_PROMPT = """
Ты — агент реанимации диалога.

Твоя задача:
Тебе передаётся диалог между клиентом и ассистентом.
Диалог может быть прерван, завис, потерять смысл, эмоционально «остыть» или зайти в тупик.
Твоя цель — сформулировать ОДИН естественный вопрос, который поможет вернуть клиента в диалог и продолжить разговор.

Правила:
- Верни ТОЛЬКО один вопрос.
- Не добавляй пояснений, приветствий, извинений или мета-комментариев.
- Вопрос должен опираться только на контекст диалога.
- Вопрос должен двигать диалог вперёд, а не возвращаться назад.
- Избегай вопросов «да/нет», если это не критично.
- Не повторяй последний ответ ассистента.
- Не вводи новые темы, не связанные с диалогом.
- Не задавай несколько вопросов в одном предложении.
- Не дави на клиента и не делай предположений о его намерениях.

Рекомендации:
- Если клиент перестал отвечать — задай мягкий уточняющий или продолжающий вопрос.
- Если клиент выглядит растерянным — задай упрощающий или фокусирующий вопрос.
- Если в диалоге есть раздражение или сомнение — задай поддерживающий, заземляющий вопрос.
- Если диалог без направления — помоги выбрать следующий шаг.
- Если не хватает информации — спроси о самом важном недостающем факте.

Тон:
- Спокойный
- Уважительный
- Ненавязчивый
- Человечный

Формат вывода:
Обычный текст.
Только вопрос.
Без лишних символов.
""".strip()


_lock = asyncio.Lock()
_agent_redialog: Optional[CompiledStateGraph] = None


async def get_agent_redialog() -> CompiledStateGraph:
    """
    Ленивая (и безопасная) инициализация агента на процесс.

    Контракт:
    - возвращает CompiledStateGraph (подграф/агент),
    - кешируется на процесс,
    - безопасно при конкурентных вызовах.
    """
    global _agent_redialog
    if _agent_redialog is not None:
        return _agent_redialog

    async with _lock:
        if _agent_redialog is not None:
            return _agent_redialog

        # гарантируем поднятие ресурсов + моделей
        await init_resources()
        models = get_models()

        _agent_redialog = create_agent(
            model=models.model_4o_mini,
            state_schema=AgentState,
            system_prompt=_SYSTEM_PROMPT,
        )
        return _agent_redialog
