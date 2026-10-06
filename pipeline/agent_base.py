"""
Base agentic loop shared by all fleet agents.

LLM backend: any OpenAI-compatible provider (Groq, OpenRouter, OpenAI, Ollama, …).
    SDK      : openai (pip install openai)
    Key env  : LLM_GATEWAY_API_KEY   — required; your provider API key
    URL env  : LLM_GATEWAY_API_URL   — required; provider base URL
    Model env: LLM_GATEWAY_MODEL     — required; model name

All three must be set in .env — the pipeline exits with a clear error if any are missing.

Pattern: the configured LLM decides which MemClaw MCP tools to call.
  1. Discover tools from MCP server (tools/list)
  2. Convert MCP tool schemas → OpenAI function-calling format
  3. Send system + user prompt to the configured model with tools attached
  4. If the model returns tool_calls → execute each via MCP, feed results back
  5. Repeat until the model stops issuing tool calls (finish_reason == "stop")
  6. Return the final text response + a log of every tool call made
"""

import os
import json
import time
import logging
import mcp_client as mcp
from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError
from typing import Any

from retry import LLM_CONNECTION_BASE_SECONDS, LLM_RATE_LIMIT_BASE_SECONDS, MAX_ATTEMPTS, backoff_delay

log = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 4096

_client = None  # lazy singleton


def _model() -> str:
    model = os.environ.get("LLM_GATEWAY_MODEL", "").strip()
    if not model:
        raise EnvironmentError("LLM_GATEWAY_MODEL is not set — add it to .env")
    return model


def _max_tokens() -> int:
    try:
        return max(256, int(os.environ.get("LLM_GATEWAY_MAX_TOKENS", _DEFAULT_MAX_TOKENS)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_TOKENS


def _llm() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("LLM_GATEWAY_API_KEY", "").strip()
        base_url = os.environ.get("LLM_GATEWAY_API_URL", "").strip()
        if not api_key:
            raise EnvironmentError("LLM_GATEWAY_API_KEY is not set — add it to .env")
        if not base_url:
            raise EnvironmentError("LLM_GATEWAY_API_URL is not set — add it to .env")
        _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def _to_openai_tools(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool schemas to OpenAI function-calling format."""
    result = []
    for t in mcp_tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result


def run_agent(
    agent_id: str,
    system: str,
    user_prompt: str,
    allowed_tools: list[str] | None = None,
    max_iterations: int = 10,
) -> dict[str, Any]:
    """
    Run a single agent through the MCP tool-use loop.

    Args:
        agent_id:       MemClaw agent identifier (injected into every MCP call)
        system:         System prompt describing this agent's role
        user_prompt:    The task prompt for this turn
        allowed_tools:  If set, only expose these tool names to the model.
                        Pass [] for no tools. Pass None for all tools.
        max_iterations: Safety cap on tool-use rounds

    Returns:
        {
          "agent_id":    str,
          "final_text":  str,
          "tool_calls":  list[dict],
          "iterations":  int,
        }
    """
    all_tools = mcp.list_tools(agent_id=agent_id)
    if allowed_tools is None:
        tools = all_tools
    else:
        allowed_set = set(allowed_tools)
        tools = [t for t in all_tools if t["name"] in allowed_set]

    openai_tools = _to_openai_tools(tools)

    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user",   "content": user_prompt},
    ]
    tool_call_log: list[dict] = []
    final_text = ""
    iterations = 0

    log.info("[%s] Starting — tools: %s", agent_id, [t["name"] for t in tools])

    while iterations < max_iterations:
        iterations += 1

        kwargs: dict[str, Any] = {
            "model":      _model(),
            "messages":   messages,
            "max_tokens": _max_tokens(),
        }
        if openai_tools:
            kwargs["tools"] = openai_tools
            if iterations == 1:
                kwargs["tool_choice"] = "required"

        # Retry on 429 rate limit with exponential backoff
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = _llm().chat.completions.create(**kwargs)
                break
            except APIStatusError as exc:
                if exc.status_code == 429 and attempt < MAX_ATTEMPTS - 1:
                    wait = backoff_delay(LLM_RATE_LIMIT_BASE_SECONDS, attempt)
                    log.warning(
                        "[%s] rate limited — waiting %.1fs (attempt %d/%d)",
                        agent_id,
                        wait,
                        attempt + 1,
                        MAX_ATTEMPTS,
                    )
                    time.sleep(wait)
                else:
                    raise
            except (APIConnectionError, APITimeoutError) as exc:
                if attempt < MAX_ATTEMPTS - 1:
                    wait = backoff_delay(LLM_CONNECTION_BASE_SECONDS, attempt)
                    log.warning("[%s] LLM connection error: %s — retrying in %.1fs",
                                agent_id, exc, wait)
                    time.sleep(wait)
                else:
                    raise

        choice  = response.choices[0]
        message = choice.message

        if message.content:
            final_text = message.content

        tool_calls = message.tool_calls or []

        if not tool_calls:
            log.info("[%s] Finished after %d iteration(s)", agent_id, iterations)
            break

        messages.append(message)

        for tc in tool_calls:
            tool_name = tc.function.name

            try:
                tool_input = json.loads(tc.function.arguments)
            except json.JSONDecodeError as exc:
                # LLM returned malformed JSON — log and skip this tool call
                log.error("[%s] Malformed tool arguments for %s: %s | raw: %r",
                          agent_id, tool_name, exc, tc.function.arguments)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      json.dumps({"error": f"Malformed arguments: {exc}"}),
                })
                tool_call_log.append({
                    "tool":   tool_name,
                    "input":  None,
                    "result": {"error": f"Malformed arguments: {exc}"},
                    "status": "parse_error",
                })
                continue

            log.debug("[%s] → %s(%s)", agent_id, tool_name, _summarise(tool_input))

            try:
                result = mcp.call_tool(tool_name, tool_input, agent_id=agent_id)
                status = "ok"
            except ValueError as exc:
                result = {"error": str(exc)}
                status = "error"
                log.error("[%s] Tool %s failed — unknown tool (check allowlist): %s", agent_id, tool_name, exc)
            except Exception as exc:
                result = {"error": str(exc)}
                status = "error"
                log.error("[%s] Tool %s failed — transport error: %s", agent_id, tool_name, exc)

            tool_call_log.append({
                "tool":   tool_name,
                "input":  tool_input,
                "result": result,
                "status": status,
            })
            log.info("[%s] ← %s %s: %s", agent_id, tool_name, status, _summarise(result))

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      _tool_message_content(tool_name, result),
            })

    else:
        # The while loop exhausted max_iterations without the model ever stopping, so
        # `final_text` is whatever the last response happened to contain and the run is
        # not a normal completion. Without this the cap silently truncates a run — and
        # because the model was still asking for tools, the work is genuinely unfinished
        # rather than merely at a boundary.
        log.warning(
            "[%s] hit max_iterations (%d) with tool calls still pending",
            agent_id,
            max_iterations,
        )

    return {
        "agent_id":   agent_id,
        "final_text": final_text,
        "tool_calls": tool_call_log,
        "iterations": iterations,
    }


def _summarise(obj: Any, max_len: int = 120) -> str:
    s = json.dumps(obj) if not isinstance(obj, str) else obj
    return s[:max_len] + "…" if len(s) > max_len else s


def _tool_message_content(tool_name: str, result: Any, max_len: int = 4000) -> str:
    """Compact large tool payloads before sending them back to the model."""
    if isinstance(result, str):
        return result[:max_len] + "…" if len(result) > max_len else result

    if not isinstance(result, dict):
        text = json.dumps(result)
        return text[:max_len] + "…" if len(text) > max_len else text

    compact: dict[str, Any] = result

    if tool_name in {"memclaw_recall", "memclaw_list"}:
        compact = {k: result.get(k) for k in ("query", "summary", "focus", "memory_count", "total", "total_memories", "next_cursor") if k in result}
        raw_list = result.get("results") or result.get("items") or []
        if isinstance(raw_list, list):
            compact["results"] = []
            for item in raw_list[:5]:
                if not isinstance(item, dict):
                    compact["results"].append(item)
                    continue
                compact["results"].append({
                    "id": item.get("id"),
                    "agent_id": item.get("agent_id"),
                    "memory_type": item.get("memory_type") or item.get("type"),
                    "title": item.get("title"),
                    "content": _truncate_text(item.get("content", ""), 400),
                })

    elif tool_name == "memclaw_insights":
        compact = {
            "focus": result.get("focus"),
            "total_memories": result.get("total_memories"),
            "by_agent": result.get("by_agent"),
            "contradictions": result.get("contradictions", [])[:5],
            "patterns": result.get("patterns", [])[:5],
            "stale_count": result.get("stale_count"),
        }

    text = json.dumps(compact, ensure_ascii=False)
    return text[:max_len] + "…" if len(text) > max_len else text


def _truncate_text(value: Any, max_len: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:max_len] + "…" if len(text) > max_len else text
