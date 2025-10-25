import logging

from typing import Dict, Any
from langchain_core.messages import AIMessage


logger = logging.getLogger(__name__)

# --- Fallback для подсчёта токенов, если usage недоступен ---
try:
    import tiktoken
except ImportError:
    tiktoken = None

def _get_encoder(model_name: str = "gpt-4o-mini-2024-07-18"):
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model(model_name)
    except Exception:
        try:
            return tiktoken.get_encoding("o200k_base")
        except Exception:
            return None

def _count_tokens_text(text: str, enc) -> int:
    if not enc or not isinstance(text, str):
        return 0
    return len(enc.encode(text or ""))

def _count_tokens_messages(messages: list, enc) -> int:
    if not enc:
        return 0
    total = 0
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            total += _count_tokens_text(c, enc)
        elif isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict):
                    if "text" in p and isinstance(p["text"], str):
                        parts.append(p["text"])
                    elif "content" in p and isinstance(p["content"], str):
                        parts.append(p["content"])
            total += _count_tokens_text(" ".join(parts), enc)
    return total

def _extract_usage(ai_msg: AIMessage) -> Dict[str, int]:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "prompt_cached": 0, "completion_reasoning": 0}
    um = getattr(ai_msg, "usage_metadata", None) or {}
    rm = getattr(ai_msg, "response_metadata", {}) or {}
    tu = (rm.get("token_usage") or rm.get("usage") or {})  # alternate key

    inp = um.get("input_tokens")
    out = um.get("output_tokens")
    total = um.get("total_tokens")

    if inp is None:  inp = tu.get("prompt_tokens")
    if out is None:  out = tu.get("completion_tokens")
    if total is None: total = tu.get("total_tokens")

    usage["input_tokens"] = int(inp or 0)
    usage["output_tokens"] = int(out or 0)
    usage["total_tokens"] = int(total or 0)

    # details
    prompt_details = tu.get("prompt_tokens_details", {}) or um.get("input_token_details", {}) or {}
    completion_details = tu.get("completion_tokens_details", {}) or um.get("output_token_details", {}) or {}

    usage["prompt_cached"] = int((prompt_details.get("cached_tokens") or prompt_details.get("cache_read") or 0) or 0)
    usage["completion_reasoning"] = int((completion_details.get("reasoning") or completion_details.get("reasoning_tokens") or 0) or 0)
    return usage

def _ensure_tokens_state(state: Dict[str, Any]) -> Dict[str, int]:
    tokens = state.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {
            "prompt": 0,
            "completion": 0,
            "total_llm": 0,
            "prompt_cached": 0,
            "completion_reasoning": 0,
            "tool_in": 0,
            "tool_out": 0,
            "total_overall": 0,
        }
        state["tokens"] = tokens
    else:
        # гарантируем наличие всех ключей
        for k in ["prompt","completion","total_llm","prompt_cached","completion_reasoning","tool_in","tool_out","total_overall"]:
            tokens.setdefault(k, 0)
    return tokens