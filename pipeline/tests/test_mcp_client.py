"""
Unit tests for mcp_client — mocks all HTTP calls so no real API keys needed.
Run:  cd pipeline && pytest tests/ -v
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

# Make pipeline/ importable from tests/
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Provide stub env vars so modules load without errors
os.environ.setdefault("MEMCLAW_API_KEY",   "test-key")
os.environ.setdefault("MEMCLAW_TENANT_ID", "test-tenant")
os.environ.setdefault("MEMCLAW_FLEET_ID",  "test-fleet")
os.environ.setdefault("MEMCLAW_TRANSPORT", "rest")
os.environ.setdefault("LLM_GATEWAY_API_KEY", "test-llm-key")

import mcp_client as mcp


def _json_response(data: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {"Content-Type": "application/json"}
    r.json.return_value = data
    r.raise_for_status = MagicMock()
    return r


def _sse_response(data: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {"Content-Type": "text/event-stream"}
    r.text = "event: message\n" f"data: {json.dumps(data)}\n\n"
    r.json.side_effect = AssertionError("SSE response should not call json()")
    r.raise_for_status = MagicMock()
    return r


def _reset_mcp_state():
    mcp._mcp_request_id = 0
    mcp._mcp_session_id = None
    mcp._mcp_initialized = False
    mcp._mcp_tools_cache = None


# ── _api URL validation ───────────────────────────────────────────────────────

def test_api_rejects_http_url(monkeypatch):
    monkeypatch.setenv("MEMCLAW_API_URL", "http://evil.com")
    with pytest.raises(ValueError, match="must start with https://"):
        mcp._api("memories")


def test_api_accepts_https_url(monkeypatch):
    monkeypatch.setenv("MEMCLAW_API_URL", "https://memclaw.net")
    url = mcp._api("memories")
    assert url == "https://memclaw.net/api/v1/memories"


def test_mcp_url_rejects_http_url(monkeypatch):
    monkeypatch.setenv("MEMCLAW_MCP_URL", "http://evil.com/mcp")
    with pytest.raises(ValueError, match="must start with https://"):
        mcp._validate_mcp_url()


# ── _validate_memory_content ──────────────────────────────────────────────────

def test_validate_empty_content_raises():
    with pytest.raises(ValueError, match="must not be empty"):
        mcp._validate_memory_content("")


def test_validate_whitespace_only_raises():
    with pytest.raises(ValueError, match="must not be empty"):
        mcp._validate_memory_content("   ")


def test_validate_too_long_raises():
    from config import MAX_MEMORY_CONTENT_LEN
    with pytest.raises(ValueError, match="too long"):
        mcp._validate_memory_content("x" * (MAX_MEMORY_CONTENT_LEN + 1))


def test_validate_valid_content():
    result = mcp._validate_memory_content("This is a valid memory.")
    assert result == "This is a valid memory."


# ── _write ────────────────────────────────────────────────────────────────────

@patch("mcp_client.requests.post")
def test_write_single_memory(mock_post):
    mock_post.return_value = _json_response({"id": "mem-1", "status": "created"}, 201)
    result = mcp._write({
        "agent_id":   "test-agent",
        "content":    "Use CSS Grid for layout",
        "type":       "decision",
        "importance": 0.9,
        "tags":       ["css", "layout"],
    })
    assert result["written"] == 1
    assert mock_post.call_count == 1
    body = mock_post.call_args.kwargs["json"]
    assert body["content"] == "Use CSS Grid for layout"
    assert body["type"] == "decision"


@patch("mcp_client.requests.post")
def test_write_batch_memories(mock_post):
    mock_post.return_value = _json_response({"id": "mem-x"}, 201)
    result = mcp._write({
        "agent_id": "test-agent",
        "memories": [
            {"content": "Memory A", "type": "fact", "importance": 0.8},
            {"content": "Memory B", "type": "rule", "importance": 0.9},
        ],
    })
    assert result["written"] == 2
    assert mock_post.call_count == 2


def test_write_empty_content_raises():
    with pytest.raises(ValueError, match="must not be empty"):
        mcp._write({"agent_id": "a", "content": "", "type": "fact", "importance": 0.5})


# ── _recall ───────────────────────────────────────────────────────────────────

@patch("mcp_client.requests.post")
def test_recall_passes_query(mock_post):
    mock_post.return_value = _json_response({"items": []})
    mcp._recall({"agent_id": "agent-1", "query": "CSS layout", "top_k": 5})
    body = mock_post.call_args.kwargs["json"]
    assert body["query"] == "CSS layout"
    assert body["top_k"] == 5


# ── _manage ───────────────────────────────────────────────────────────────────

def test_manage_missing_memory_id_raises():
    with pytest.raises(ValueError, match="memory_id is required"):
        mcp._manage({"op": "read", "memory_id": ""})


def test_manage_unsupported_op_raises():
    with pytest.raises(ValueError, match="Unsupported op"):
        mcp._manage({"op": "patch", "memory_id": "mem-1"})


@patch("mcp_client.requests.get")
def test_manage_read(mock_get):
    mock_get.return_value = _json_response({"id": "mem-1", "content": "test"})
    result = mcp._manage({"op": "read", "memory_id": "mem-1"})
    assert result["id"] == "mem-1"


# ── _insights contradiction detection ────────────────────────────────────────

@patch("mcp_client.requests.get")
def test_insights_no_false_positive_same_type_no_negation(mock_get):
    """Two agents discussing similar topics without negation should NOT be flagged."""
    items = [
        {"id": "m1", "agent_id": "agent-a", "type": "decision",
         "content": "use CSS Grid for layout with flex inside cells"},
        {"id": "m2", "agent_id": "agent-b", "type": "decision",
         "content": "CSS Grid layout with flex for inner components"},
    ]
    # _insights calls _get("memories") then _get("memories/stats") — return items then stats
    mock_get.side_effect = [
        _json_response({"items": items, "total": 2}),
        _json_response({"total": 2}),
    ]
    result = mcp._insights({})
    assert result["contradictions"] == []


@patch("mcp_client.requests.get")
def test_insights_detects_negation_contradiction(mock_get):
    """One agent says X, another says 'no X' — same type, same topic → contradiction."""
    items = [
        {"id": "m1", "agent_id": "agent-a", "type": "rule",
         "content": "external JavaScript libraries are allowed for schema markup"},
        {"id": "m2", "agent_id": "agent-b", "type": "rule",
         "content": "no external JavaScript libraries allowed zero bundle size"},
    ]
    mock_get.side_effect = [
        _json_response({"items": items, "total": 2}),
        _json_response({"total": 2}),
    ]
    result = mcp._insights({})
    assert len(result["contradictions"]) == 1
    assert "agent-a" in result["contradictions"][0]["agents"]


# ── call_tool dispatch ────────────────────────────────────────────────────────

def test_call_tool_unknown_raises():
    with pytest.raises(ValueError, match="Unknown tool"):
        mcp.call_tool("memclaw_nonexistent", {})


@patch("mcp_client._recall")
def test_call_tool_injects_agent_id(mock_recall):
    mock_recall.return_value = {"items": []}
    mcp.call_tool("memclaw_recall", {"query": "test"}, agent_id="my-agent")
    args = mock_recall.call_args[0][0]
    assert args["agent_id"] == "my-agent"


@patch("mcp_client.requests.post")
def test_mcp_list_tools_uses_tools_list(mock_post, monkeypatch):
    monkeypatch.setenv("MEMCLAW_TRANSPORT", "mcp")
    monkeypatch.setenv("MEMCLAW_MCP_URL", "https://memclaw.net/mcp")
    _reset_mcp_state()
    mock_post.side_effect = [
        _json_response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}),
        _json_response({}),
        _sse_response({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [{
                    "name": "memclaw_recall",
                    "description": "Recall",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                }],
            },
        }),
    ]

    tools = mcp.list_tools()
    assert tools == [{
        "name": "memclaw_recall",
        "description": "Recall",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }]
    methods = [call.kwargs["json"]["method"] for call in mock_post.call_args_list]
    assert methods == ["initialize", "notifications/initialized", "tools/list"]


@patch("mcp_client.requests.post")
def test_mcp_call_tool_uses_tools_call(mock_post, monkeypatch):
    monkeypatch.setenv("MEMCLAW_TRANSPORT", "mcp")
    monkeypatch.setenv("MEMCLAW_MCP_URL", "https://memclaw.net/mcp")
    _reset_mcp_state()
    mock_post.side_effect = [
        _json_response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}),
        _json_response({}),
        _json_response({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": "{\"items\": []}"}],
            },
        }),
    ]

    result = mcp.call_tool("memclaw_recall", {"query": "test"}, agent_id="agent-1")
    assert result == {"items": []}
    tool_call_body = mock_post.call_args_list[-1].kwargs["json"]
    assert tool_call_body["method"] == "tools/call"
    assert tool_call_body["params"]["name"] == "memclaw_recall"
    assert tool_call_body["params"]["arguments"]["agent_id"] == "agent-1"
    # tenant_id is deliberately absent: the server derives it from the API key, and
    # call_tool() strips it before dispatch (the _transport() == "mcp" branch in
    # mcp_client.py). Asserting it were forwarded would reinstate the leak that strip
    # exists to prevent.
    assert "tenant_id" not in tool_call_body["params"]["arguments"]
    assert tool_call_body["params"]["arguments"]["fleet_id"] == "test-fleet"


# ── health_check ──────────────────────────────────────────────────────────────

@patch("mcp_client.requests.get")
def test_health_check_true_on_dict(mock_get):
    mock_get.return_value = _json_response({"total": 5})
    assert mcp.health_check() is True


@patch("mcp_client.requests.get")
def test_health_check_false_on_connection_error(mock_get):
    from requests.exceptions import ConnectionError
    mock_get.side_effect = ConnectionError("refused")
    assert mcp.health_check() is False


# ── retry logic ───────────────────────────────────────────────────────────────

@patch("mcp_client.time.sleep")
@patch("mcp_client.requests.get")
def test_retry_on_500(mock_get, mock_sleep):
    """Should retry twice on 5xx then succeed on third attempt."""
    fail = MagicMock()
    fail.status_code = 500
    fail.headers = {"Content-Type": "application/json"}
    fail.raise_for_status = MagicMock()

    ok = _json_response({"total": 3})

    mock_get.side_effect = [fail, fail, ok]
    result = mcp._get("memories/stats")
    assert result == {"total": 3}
    assert mock_get.call_count == 3
    assert mock_sleep.call_count == 2  # slept before attempt 2 and 3

def test_get_mcp_url_reads_environment_at_call_time(monkeypatch):
    monkeypatch.setenv("MEMCLAW_API_URL", "https://first.example.com")
    monkeypatch.delenv("MEMCLAW_MCP_URL", raising=False)

    assert mcp.get_mcp_url() == "https://first.example.com/mcp"

    monkeypatch.setenv("MEMCLAW_API_URL", "https://second.example.com")

    assert mcp.get_mcp_url() == "https://second.example.com/mcp"


def test_get_mcp_url_prefers_explicit_mcp_url(monkeypatch):
    monkeypatch.setenv("MEMCLAW_API_URL", "https://api.example.com")
    monkeypatch.setenv("MEMCLAW_MCP_URL", "https://custom.example.com/mcp")

    assert mcp.get_mcp_url() == "https://custom.example.com/mcp"


def test_get_base_url_reads_environment_at_call_time(monkeypatch):
    monkeypatch.setenv("MEMCLAW_API_URL", "https://first.example.com")
    assert mcp.get_base_url() == "https://first.example.com"

    monkeypatch.setenv("MEMCLAW_API_URL", "https://second.example.com")
    assert mcp.get_base_url() == "https://second.example.com"
