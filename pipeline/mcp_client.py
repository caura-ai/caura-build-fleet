"""
MemClaw MCP client.

Default runtime path:
  POST https://memclaw.net/mcp      — MCP Streamable HTTP JSON-RPC
  methods: initialize, tools/list, tools/call
  Auth: X-API-Key header

Compatibility path:
  Set MEMCLAW_TRANSPORT=rest to use the local REST-backed tool facade. This keeps
  tests and offline development useful, but production runs should use MCP.
"""

import os
import json
import time
import logging
import requests
from requests.exceptions import RequestException, Timeout, HTTPError, ConnectionError as RequestsConnectionError
from typing import Any

from retry import MEMCLAW_BASE_SECONDS, MEMCLAW_MAX_ATTEMPTS, backoff_delay

from config import MAX_MEMORY_CONTENT_LEN, MEMCLAW_API_DOMAIN

log = logging.getLogger(__name__)

MAX_RECALL_TOP_K = 20
MCP_PROTOCOL_VERSION = "2025-03-26"

_mcp_request_id = 0
_mcp_session_id: str | None = None
_mcp_initialized = False
_mcp_tools_cache: list[dict] | None = None
_mcp_transport_at_cache: str | None = None  # transport value when cache was last populated


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def get_base_url() -> str:
    return _cfg("MEMCLAW_API_URL", f"https://{MEMCLAW_API_DOMAIN}")


def get_mcp_url() -> str:
    return _cfg(
        "MEMCLAW_MCP_URL",
        f"{get_base_url().rstrip('/')}/mcp",
    )

def _headers() -> dict:
    return {
        "X-API-Key": _cfg("MEMCLAW_API_KEY"),
        "Content-Type": "application/json",
    }


def _mcp_headers(agent_id: str | None = None) -> dict:
    headers = {
        "X-API-Key": _cfg("MEMCLAW_API_KEY"),
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if _mcp_session_id:
        headers["Mcp-Session-Id"] = _mcp_session_id
    if agent_id:
        headers["X-Agent-ID"] = agent_id
    return headers


def _base() -> dict:
    return {
        "fleet_id":  _cfg("MEMCLAW_FLEET_ID", "fleet"),
        "tenant_id": _cfg("MEMCLAW_TENANT_ID"),
    }


def _api(path: str) -> str:
    base = _cfg("MEMCLAW_API_URL", f"https://{MEMCLAW_API_DOMAIN}")
    # Validate URL to prevent SSRF via env var injection
    if not base.startswith("https://"):
        raise ValueError(f"MEMCLAW_API_URL must start with https://, got: {base!r}")
    return f"{base.rstrip('/')}/api/v1/{path.lstrip('/')}"


def _transport() -> str:
    return _cfg("MEMCLAW_TRANSPORT", "mcp").strip().lower()

def _validate_mcp_url() -> str:
    url = get_mcp_url()
    if not url.startswith("https://"):
        raise ValueError(
            f"MEMCLAW_MCP_URL must start with https://, got: {url!r}"
        )
    return url

def _next_mcp_id() -> int:
    global _mcp_request_id
    _mcp_request_id += 1
    return _mcp_request_id


def _decode_json_or_sse(response: requests.Response) -> dict:
    ct = response.headers.get("Content-Type", "")
    if "application/json" in ct:
        return response.json()

    if "text/event-stream" not in ct:
        raise ValueError(f"Expected JSON or SSE response, got Content-Type: {ct!r}")

    data_lines: list[str] = []
    for raw_line in response.text.splitlines():
        line = raw_line.strip()
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    if data_lines:
        return json.loads("\n".join(data_lines))
    raise ValueError("SSE response did not contain a JSON data event")


def _mcp_json_rpc(method: str, params: dict | None = None, *, expect_response: bool = True, agent_id: str | None = None) -> dict:
    url = _validate_mcp_url()
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    }
    if expect_response:
        body["id"] = _next_mcp_id()

    for attempt in range(MEMCLAW_MAX_ATTEMPTS):
        try:
            response = requests.post(url, headers=_mcp_headers(agent_id), json=body, timeout=60)

            global _mcp_session_id
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                _mcp_session_id = session_id

            if 400 <= response.status_code < 500:
                log.error("MemClaw MCP %s → %d: %s", method, response.status_code, response.text[:500])
                response.raise_for_status()

            if response.status_code >= 500:
                if attempt < MEMCLAW_MAX_ATTEMPTS - 1:
                    wait = backoff_delay(MEMCLAW_BASE_SECONDS, attempt)
                    log.warning("MemClaw MCP %s → %d, retrying in %.1fs", method, response.status_code, wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()

            if not expect_response:
                return {}

            payload = _decode_json_or_sse(response)
            if "error" in payload:
                raise RequestException(f"MCP {method} error: {payload['error']}")
            return payload.get("result", payload)

        except (Timeout, RequestsConnectionError) as exc:
            if attempt < MEMCLAW_MAX_ATTEMPTS - 1:
                wait = backoff_delay(MEMCLAW_BASE_SECONDS, attempt)
                log.warning("MemClaw MCP %s network error: %s — retrying in %.1fs", method, exc, wait)
                time.sleep(wait)
                continue
            raise RequestException(
                f"MemClaw MCP {method} failed after {MEMCLAW_MAX_ATTEMPTS} attempts: {exc}"
            ) from exc

    raise RequestException(f"MemClaw MCP {method} exhausted retries")


def _mcp_reset_session() -> None:
    global _mcp_initialized, _mcp_session_id, _mcp_tools_cache, _mcp_transport_at_cache
    _mcp_initialized = False
    _mcp_session_id = None
    _mcp_tools_cache = None
    _mcp_transport_at_cache = None


def _mcp_initialize() -> None:
    global _mcp_initialized
    if _mcp_initialized:
        return

    _mcp_json_rpc("initialize", {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {
            "name": "memclaw-fleet",
            "version": "0.1.0",
        },
    })
    # Streamable HTTP servers accept this notification after initialize. If a
    # server ignores notifications, the request still keeps the session warm.
    try:
        _mcp_json_rpc("notifications/initialized", {}, expect_response=False)
    except Exception as exc:
        log.debug("MCP initialized notification failed non-fatally: %s", exc)
    _mcp_initialized = True


def _normalise_tool_schema(tool: dict) -> dict:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {
        "type": "object",
        "properties": {},
    }
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "input_schema": schema,
    }


def _mcp_list_tools() -> list[dict]:
    global _mcp_tools_cache, _mcp_transport_at_cache
    current_transport = _transport()
    if _mcp_tools_cache is not None and _mcp_transport_at_cache == current_transport:
        return _mcp_tools_cache
    if _mcp_transport_at_cache is not None and _mcp_transport_at_cache != current_transport:
        # Transport changed since the cache was built — reset all MCP session state.
        _mcp_reset_session()

    _mcp_initialize()
    result = _mcp_json_rpc("tools/list")
    raw_tools = result.get("tools", result if isinstance(result, list) else [])
    _mcp_tools_cache = [_normalise_tool_schema(t) for t in raw_tools]
    _mcp_transport_at_cache = current_transport
    return _mcp_tools_cache


def _extract_tool_result(result: dict) -> Any:
    if "structuredContent" in result:
        return result["structuredContent"]

    content = result.get("content")
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except json.JSONDecodeError:
                return texts[0]
        if texts:
            return "\n".join(texts)

    return result


def _mcp_call_tool(tool_name: str, args: dict) -> Any:
    _mcp_initialize()
    result = _mcp_json_rpc("tools/call", {
        "name": tool_name,
        "arguments": args,
    }, agent_id=args.get("agent_id"))
    if result.get("isError"):
        extracted = _extract_tool_result(result)
        # CONFLICT means a near-duplicate already exists — treat as a successful no-op
        # so the model doesn't loop retrying the same write.
        if isinstance(extracted, dict) and extracted.get("error", {}).get("code") == "CONFLICT":
            existing_id = extracted["error"].get("message", "").split(":")[-1].strip()
            return {"status": "duplicate", "existing_id": existing_id, "agent_id": args.get("agent_id")}
        raise RequestException(f"MCP tool {tool_name} returned error: {extracted}")
    return _extract_tool_result(result)


def _request_with_retry(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    max_attempts: int = MEMCLAW_MAX_ATTEMPTS,
) -> dict:
    """
    Make an HTTP request to the MemClaw API with exponential backoff retry.
    Retries on transient errors (5xx, Timeout, connection errors).
    Raises on 4xx (caller's fault) or after max_attempts exhausted.
    """
    url = _api(path)
    for attempt in range(max_attempts):
        try:
            if method == "GET":
                r = requests.get(url, headers=_headers(), params=params, timeout=30)
            elif method == "POST":
                r = requests.post(url, headers=_headers(), json=json_body, timeout=30)
            elif method == "DELETE":
                r = requests.delete(url, headers=_headers(), timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # 4xx → caller error, don't retry. Log response body to aid debugging.
            if 400 <= r.status_code < 500:
                ct = r.headers.get("Content-Type", "")
                body_preview = None
                try:
                    if "application/json" in ct:
                        body_preview = r.json()
                    else:
                        body_preview = r.text
                except Exception:
                    body_preview = r.text if hasattr(r, "text") else "<unavailable>"
                log.error("MemClaw %s %s → %d (client error): %s", method, path, r.status_code, body_preview)
                r.raise_for_status()

            # 5xx → transient, retry with backoff
            if r.status_code >= 500:
                if attempt < max_attempts - 1:
                    wait = backoff_delay(MEMCLAW_BASE_SECONDS, attempt)
                    log.warning("MemClaw %s %s → %d, retrying in %.1fs (attempt %d/%d)",
                                method, path, r.status_code, wait, attempt + 1, max_attempts)
                    time.sleep(wait)
                    continue
                r.raise_for_status()

            # Validate JSON response
            ct = r.headers.get("Content-Type", "")
            if "application/json" not in ct:
                raise ValueError(f"Expected JSON response, got Content-Type: {ct!r}")

            return r.json()

        except (Timeout, RequestsConnectionError) as exc:
            if attempt < max_attempts - 1:
                wait = backoff_delay(MEMCLAW_BASE_SECONDS, attempt)
                log.warning("MemClaw %s %s network error: %s — retrying in %.1fs (%d/%d)",
                            method, path, exc, wait, attempt + 1, max_attempts)
                time.sleep(wait)
            else:
                raise RequestException(f"MemClaw {method} {path} failed after {max_attempts} attempts: {exc}") from exc

    raise RequestException(f"MemClaw {method} {path} exhausted {max_attempts} retries")


def _post(path: str, body: dict) -> dict:
    return _request_with_retry("POST", path, json_body={**_base(), **body})


def _get(path: str, params: dict | None = None) -> dict:
    return _request_with_retry("GET", path, params={**_base(), **(params or {})})


# Keys injected by _base()/_post()/_get() that must never be forwarded as extra
# user-supplied filter params — stripping them prevents double-keying and leaking
# internal routing fields to endpoints that don't expect them.
_INTERNAL_KEYS = frozenset({"fleet_id", "fleet_ids", "tenant_id"})


def _filter_params(args: dict) -> dict:
    """Return args with internal routing keys removed. Uses 'is not None' so that
    legitimate zero/False/empty-string filter values are preserved."""
    return {k: v for k, v in args.items() if v is not None and k not in _INTERNAL_KEYS}


# ── Tool implementations ──────────────────────────────────────────────────────

def call_tool(tool_name: str, arguments: dict, agent_id: str | None = None) -> Any:
    args = dict(arguments)
    if agent_id and "agent_id" not in args:
        args["agent_id"] = agent_id
    for key, value in _base().items():
        if value and key not in args:
            args[key] = value

    # Clamp recall requests early so a model-requested top_k cannot exceed the API limit.
    if tool_name == "memclaw_recall" and "top_k" in args:
        try:
            args["top_k"] = max(1, min(int(args["top_k"]), MAX_RECALL_TOP_K))
        except (TypeError, ValueError):
            args["top_k"] = MAX_RECALL_TOP_K

    if _transport() == "mcp":
        # Strip tenant_id — the server derives it from the API key.
        # Force fleet_id from env: the model may send fleet_id="" which blocks
        # server-side injection; override it with the authoritative env value.
        mcp_args = {k: v for k, v in args.items() if k != "tenant_id"}
        mcp_args["fleet_id"] = _cfg("MEMCLAW_FLEET_ID", "fleet")
        return _mcp_call_tool(tool_name, mcp_args)

    dispatch = {
        "memclaw_write":      _write,
        "memclaw_recall":     _recall,
        "memclaw_list":       _list,
        "memclaw_insights":   _insights,
        "memclaw_stats":      _stats,
        "memclaw_manage":     _manage,
        "memclaw_keystones":  _keystones,
        "memclaw_entity_get": _entity_get,
        "memclaw_tune":       _tune,
        "memclaw_evolve":     _evolve,
    }
    fn = dispatch.get(tool_name)
    if not fn:
        raise ValueError(f"Unknown tool: {tool_name!r}")
    return fn(args)


def _validate_memory_content(content: str) -> str:
    if not content or not content.strip():
        raise ValueError("Memory content must not be empty")
    if len(content) > MAX_MEMORY_CONTENT_LEN:
        raise ValueError(
            f"Memory content too long: {len(content)} chars (max {MAX_MEMORY_CONTENT_LEN})"
        )
    return content


def _write(args: dict) -> dict:
    """
    Write memories. Accepts either:
      - memories: [{content, type, importance, tags}, ...]   (batch)
      - content, type, importance, tags                      (single)
    Writes each memory individually via POST /memories (returns 201).
    """
    memories = args.get("memories") or []
    if not memories:
        memories = [{
            "content":    args.get("content", ""),
            "type":       args.get("type", "fact"),
            "importance": args.get("importance", 0.8),
            "tags":       args.get("tags", []),
        }]

    agent_id = args.get("agent_id", "")
    results = []
    for mem in memories:
        content = _validate_memory_content(mem.get("content", ""))
        body = {
            "agent_id":   agent_id,
            "content":    content,
            "type":       mem.get("type", "fact"),
            "importance": mem.get("importance", 0.8),
            "tags":       mem.get("tags", []),
        }
        try:
            result = _request_with_retry("POST", "memories", json_body={**_base(), **body})
            results.append(result)
            log.debug("Wrote memory for agent=%s type=%s", agent_id, body["type"])
        except HTTPError as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 409:
                duplicate_result = None
                try:
                    duplicate_result = response.json()
                except Exception:
                    duplicate_result = {"error": getattr(response, "text", "duplicate memory")}
                log.warning("Duplicate memory for agent=%s type=%s treated as no-op", agent_id, body["type"])
                results.append({"status": "duplicate", "memory": duplicate_result})
                continue
            raise

    return {"written": len(results), "memories": results}


def _recall(args: dict) -> dict:
    top_k = args.get("top_k", 10)
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        top_k = 10
    top_k = max(1, min(top_k, MAX_RECALL_TOP_K))
    # fleet_ids is an array; caller may supply it directly, otherwise derive from env.
    fleet_ids = args.get("fleet_ids") or [_cfg("MEMCLAW_FLEET_ID", "fleet")]
    if isinstance(fleet_ids, str):
        fleet_ids = [fleet_ids]
    return _post("recall", {
        "agent_id":  args.get("agent_id", ""),
        "query":     args.get("query", ""),
        "top_k":     top_k,
        "fleet_ids": fleet_ids,
    })


def _list(args: dict) -> dict:
    return _get("memories", _filter_params(args))


def _stats(args: dict) -> dict:
    return _get("memories/stats")


def _insights(args: dict) -> dict:
    try:
        memories = _get("memories")
        stats    = _get("memories/stats")
        items    = memories.get("items", [])

        # Group by agent
        by_agent: dict[str, list] = {}
        for m in items:
            aid = m.get("agent_id", "unknown")
            by_agent.setdefault(aid, []).append(m)

        # Contradiction check: flag memories where two agents make claims of the
        # SAME type about the SAME topic but with conflicting keywords (negation).
        # Uses negation-word detection rather than naïve overlap, to reduce false positives.
        NEGATION_MARKERS = {"no", "not", "never", "without", "avoid", "don't", "zero", "none"}
        contradictions = []
        agents = list(by_agent.keys())

        for i, a1 in enumerate(agents):
            for a2 in agents[i + 1:]:
                for m1 in by_agent[a1]:
                    for m2 in by_agent[a2]:
                        c1 = m1.get("content", "").lower()
                        c2 = m2.get("content", "").lower()
                        if not c1 or not c2:
                            continue
                        w1 = set(c1.split())
                        w2 = set(c2.split())
                        overlap = len(w1 & w2) / max(len(w1 | w2), 1)
                        # Only flag when: high overlap (same topic) AND one has negation the
                        # other lacks AND same memory type (same kind of claim)
                        neg1 = bool(w1 & NEGATION_MARKERS)
                        neg2 = bool(w2 & NEGATION_MARKERS)
                        same_type = m1.get("type") == m2.get("type")
                        if overlap > 0.25 and same_type and (neg1 != neg2):
                            contradictions.append({
                                "memory_ids": [m1.get("id"), m2.get("id")],
                                "agents":     [a1, a2],
                                "overlap":    round(overlap, 2),
                                "reason":     "same-type, same-topic, negation mismatch",
                            })

        return {
            "focus":          args.get("focus", "contradictions"),
            "total_memories": stats.get("total", len(items)),
            "by_agent":       {a: len(ms) for a, ms in by_agent.items()},
            "contradictions": contradictions,
            "stale_count":    0,
            "patterns":       [f"Agent {a} wrote {len(ms)} memories" for a, ms in by_agent.items()],
        }
    except (RequestException, ValueError) as exc:
        log.error("_insights failed: %s", exc)
        return {"error": str(exc), "focus": args.get("focus", "contradictions")}


def _manage(args: dict) -> dict:
    op  = args.get("op", "read")
    mid = args.get("memory_id", "")
    if not mid:
        raise ValueError("memory_id is required for memclaw_manage")
    if op == "read":
        return _get(f"memories/{mid}")
    elif op == "delete":
        return _request_with_retry("DELETE", f"memories/{mid}")
    elif op == "update":
        body = {k: v for k, v in args.items() if k not in ("op", "memory_id")}
        return _post(f"memories/{mid}", body)
    raise ValueError(f"Unsupported op: {op!r} — must be read, update, or delete")


def _keystones(args: dict) -> dict:
    try:
        return _get("memories", {"type": "rule"})
    except (RequestException, ValueError) as exc:
        log.warning("_keystones failed: %s", exc)
        return {"keystones": []}


def _entity_get(args: dict) -> dict:
    entity_id = args.get("entity_id", "")
    # Extra filters (name, type) are forwarded as query params in both branches so
    # the server can apply them even on a by-ID lookup.
    extra = _filter_params({k: v for k, v in args.items() if k != "entity_id"})
    if entity_id:
        return _get(f"entities/{entity_id}", extra or None)
    return _get("entities", extra or None)



def _tune(args: dict) -> dict:
    return {"status": "ok", "note": "tune not available in this API version"}


def _evolve(args: dict) -> dict:
    return {"status": "ok", "note": "evolve not available in this API version"}


# ── Tool schemas for OpenAI-compatible function-calling ──────────────────────

def list_tools(agent_id: str | None = None) -> list[dict]:
    if _transport() == "mcp":
        return _mcp_list_tools()

    return [
        {
            "name": "memclaw_write",
            "description": "Write one or more memories to the fleet. Call this AFTER deciding — persist decisions, facts, and rules.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "memories": {
                        "type": "array",
                        "description": "List of memory objects to write",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content":    {"type": "string"},
                                "type":       {"type": "string", "enum": ["decision", "fact", "rule", "insight"]},
                                "importance": {"type": "number", "description": "0.0 to 1.0"},
                                "tags":       {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["content", "type", "importance"],
                        },
                    },
                },
                "required": ["memories"],
            },
        },
        {
            "name": "memclaw_recall",
            "description": "Recall relevant memories from the fleet. Always call this BEFORE acting to retrieve what previous agents decided.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query":     {"type": "string", "description": "What to search for"},
                    "top_k":     {"type": "integer", "description": "Max results (default 10, maximum 20)", "minimum": 1, "maximum": MAX_RECALL_TOP_K},
                    "fleet_ids": {"type": "array", "items": {"type": "string"}, "description": "Fleet namespaces to search (defaults to MEMCLAW_FLEET_ID)"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "memclaw_insights",
            "description": "Analyse fleet memory for contradictions, patterns, and stale entries across agents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "focus": {
                        "type": "string",
                        "enum": ["contradictions", "patterns", "stale", "divergence"],
                    },
                },
                "required": [],
            },
        },
        {
            "name": "memclaw_list",
            "description": "List all memories in the fleet. Use for auditing or when you need to enumerate rather than search.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "type":     {"type": "string"},
                },
                "required": [],
            },
        },
        {
            "name": "memclaw_stats",
            "description": "Get aggregate memory counts by type, agent, and status. Read-only.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "memclaw_keystones",
            "description": "Read mandatory governance rules for the current fleet. Read-only.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "memclaw_manage",
            "description": "Read, update, or delete a specific memory by ID.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "op":        {"type": "string", "enum": ["read", "update", "delete"]},
                    "memory_id": {"type": "string"},
                },
                "required": ["op", "memory_id"],
            },
        },
        {
            "name": "memclaw_entity_get",
            "description": "Query the knowledge graph for extracted entities and their relationships.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "Specific entity ID to fetch (optional)"},
                    "name":      {"type": "string", "description": "Filter by entity name"},
                    "type":      {"type": "string", "description": "Filter by entity type (person, org, concept)"},
                },
                "required": [],
            },
        },
    ]


def health_check() -> bool:
    try:
        if _transport() == "mcp":
            return bool(list_tools())
        result = _get("memories/stats")
        return isinstance(result, dict)
    except (RequestException, ValueError):
        return False
