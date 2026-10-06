"""
Unit tests for agent_base — mocks OpenAI client so no real LLM calls are made.
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("MEMCLAW_API_KEY",     "test-key")
os.environ.setdefault("MEMCLAW_TENANT_ID",   "test-tenant")
os.environ.setdefault("MEMCLAW_FLEET_ID",    "test-fleet")
os.environ.setdefault("LLM_GATEWAY_API_KEY", "test-llm-key")

import agent_base


def _make_choice(content: str = "", tool_calls=None, finish_reason: str = "stop"):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason
    return choice


def _make_response(choice):
    r = MagicMock()
    r.choices = [choice]
    return r


# ── _summarise ────────────────────────────────────────────────────────────────

def test_summarise_short_string():
    assert agent_base._summarise("hello") == "hello"


def test_summarise_truncates_long():
    s = "x" * 200
    result = agent_base._summarise(s)
    assert result.endswith("…")
    assert len(result) == 121  # 120 + ellipsis


def test_summarise_dict():
    result = agent_base._summarise({"key": "value"})
    assert "key" in result


@pytest.mark.parametrize("tool_name", ["memclaw_insights", "custom_tool"])
@pytest.mark.parametrize("value", ["y" * 5000, '"\\\n\t🙂' * 1200], ids=["plain", "escaped"])
def test_tool_message_oversized_dict_stays_valid_json(tool_name, value):
    payload = {"focus": "x", "by_agent": {"a": value}}
    content = agent_base._tool_message_content(tool_name, payload)

    parsed = json.loads(content)
    assert parsed["truncated"] is True
    assert parsed["preview"].startswith('{"focus": "x"')
    assert len(content) <= 4000
    assert payload == {"focus": "x", "by_agent": {"a": value}}


def test_tool_message_oversized_list_stays_valid_json():
    content = agent_base._tool_message_content("custom_tool", ['"\\\n' * 5000])
    parsed = json.loads(content)
    assert parsed["truncated"] is True
    assert parsed["preview"].startswith("[")
    assert len(content) <= 4000


@pytest.mark.parametrize("payload", [{"content": "🙂"}, ["🙂", 1], 42, None])
def test_tool_message_small_json_is_unchanged(payload):
    expected = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else json.dumps(payload)
    assert agent_base._tool_message_content("custom_tool", payload) == expected


def test_tool_message_exact_budget_is_unchanged():
    payload = {"content": "x" * 20}
    expected = json.dumps(payload, ensure_ascii=False)
    assert agent_base._tool_message_content("custom_tool", payload, max_len=len(expected)) == expected


def test_tool_message_preview_includes_json_escaping_in_budget():
    payload = {"content": '"\\\n🙂' * 100}
    content = agent_base._tool_message_content("custom_tool", payload, max_len=100)
    parsed = json.loads(content)
    assert parsed["truncated"] is True
    assert parsed["preview"]
    assert len(content) <= 100


def test_tool_message_budget_too_small_for_preview():
    with pytest.raises(ValueError, match="max_len must be at least"):
        agent_base._tool_message_content("custom_tool", {"content": "x" * 100}, max_len=10)


# ── run_agent — no tool calls (pure text response) ───────────────────────────

@patch("agent_base._llm")
@patch("mcp_client.list_tools", return_value=[])
def test_run_agent_text_only(mock_tools, mock_llm):
    mock_llm.return_value.chat.completions.create.return_value = _make_response(
        _make_choice(content="Pipeline complete.", finish_reason="stop")
    )
    result = agent_base.run_agent(
        agent_id="test-agent",
        system="You are a test agent.",
        user_prompt="Do something.",
        allowed_tools=[],
    )
    assert result["final_text"] == "Pipeline complete."
    assert result["tool_calls"] == []
    assert result["iterations"] == 1


# ── run_agent — single tool call then stop ────────────────────────────────────

@patch("agent_base._llm")
@patch("mcp_client.call_tool", return_value={"written": 1})
@patch("mcp_client.list_tools")
def test_run_agent_one_tool_call(mock_tools, mock_call_tool, mock_llm):
    mock_tools.return_value = [{
        "name": "memclaw_write",
        "description": "Write a memory",
        "input_schema": {"type": "object", "properties": {}},
    }]

    # First response: tool call
    tc = MagicMock()
    tc.id = "call-1"
    tc.function.name = "memclaw_write"
    tc.function.arguments = json.dumps({"memories": [{"content": "test", "type": "fact", "importance": 0.8}]})

    first_choice = _make_choice(tool_calls=[tc], finish_reason="tool_calls")
    second_choice = _make_choice(content="Done.", finish_reason="stop")

    mock_llm.return_value.chat.completions.create.side_effect = [
        _make_response(first_choice),
        _make_response(second_choice),
    ]

    result = agent_base.run_agent(
        agent_id="test-agent",
        system="You are a test agent.",
        user_prompt="Write a memory.",
    )
    assert result["final_text"] == "Done."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["tool"] == "memclaw_write"
    assert result["tool_calls"][0]["status"] == "ok"


# ── run_agent — the allowlist is enforced at call time, not only when exposed ──

@patch("agent_base._llm")
@patch("mcp_client.call_tool", return_value={"written": 1})
@patch("mcp_client.list_tools")
def test_run_agent_rejects_tool_outside_allowlist(mock_tools, mock_call_tool, mock_llm):
    """A read-only agent must not be able to write via a tool it never received."""
    mock_tools.return_value = [
        {"name": "memclaw_recall", "description": "Recall", "input_schema": {}},
        {"name": "memclaw_write", "description": "Write", "input_schema": {}},
    ]

    tc = MagicMock()
    tc.id = "call-sneaky"
    tc.function.name = "memclaw_write"
    tc.function.arguments = json.dumps({"content": "should never be written"})

    mock_llm.return_value.chat.completions.create.side_effect = [
        _make_response(_make_choice(tool_calls=[tc], finish_reason="tool_calls")),
        _make_response(_make_choice(content="Stopped.", finish_reason="stop")),
    ]

    result = agent_base.run_agent(
        agent_id="manager-tenant",
        system="Read-only audit.",
        user_prompt="Audit the fleet.",
        allowed_tools=["memclaw_recall"],
    )

    assert not mock_call_tool.called, "disallowed tool reached mcp.call_tool"
    assert len(result["tool_calls"]) == 1
    entry = result["tool_calls"][0]
    assert entry["tool"] == "memclaw_write"
    assert entry["status"] == "denied"
    assert "not available" in entry["result"]["error"]


@patch("agent_base._llm")
@patch("mcp_client.call_tool", return_value={"items": []})
@patch("mcp_client.list_tools")
def test_run_agent_allows_tool_inside_allowlist(mock_tools, mock_call_tool, mock_llm):
    """The enforcement must not block tools the agent legitimately holds."""
    mock_tools.return_value = [
        {"name": "memclaw_recall", "description": "Recall", "input_schema": {}},
        {"name": "memclaw_write", "description": "Write", "input_schema": {}},
    ]

    tc = MagicMock()
    tc.id = "call-ok"
    tc.function.name = "memclaw_recall"
    tc.function.arguments = json.dumps({"query": "layout"})

    mock_llm.return_value.chat.completions.create.side_effect = [
        _make_response(_make_choice(tool_calls=[tc], finish_reason="tool_calls")),
        _make_response(_make_choice(content="Done.", finish_reason="stop")),
    ]

    result = agent_base.run_agent(
        agent_id="manager-tenant",
        system="Read-only audit.",
        user_prompt="Audit the fleet.",
        allowed_tools=["memclaw_recall"],
    )

    assert mock_call_tool.called
    assert mock_call_tool.call_args[0][0] == "memclaw_recall"
    assert result["tool_calls"][0]["status"] == "ok"


@patch("agent_base._llm")
@patch("mcp_client.call_tool", return_value={"written": 1})
@patch("mcp_client.list_tools")
def test_run_agent_without_allowlist_dispatches_any_listed_tool(mock_tools, mock_call_tool, mock_llm):
    """allowed_tools=None keeps the previous behaviour: every listed tool is callable."""
    mock_tools.return_value = [
        {"name": "memclaw_write", "description": "Write", "input_schema": {}},
    ]

    tc = MagicMock()
    tc.id = "call-1"
    tc.function.name = "memclaw_write"
    tc.function.arguments = json.dumps({"content": "ok"})

    mock_llm.return_value.chat.completions.create.side_effect = [
        _make_response(_make_choice(tool_calls=[tc], finish_reason="tool_calls")),
        _make_response(_make_choice(content="Done.", finish_reason="stop")),
    ]

    result = agent_base.run_agent(
        agent_id="frontend-agent",
        system="Write memories.",
        user_prompt="Decide.",
    )

    assert mock_call_tool.called
    assert result["tool_calls"][0]["status"] == "ok"


# ── run_agent — malformed tool JSON is handled gracefully ────────────────────

@patch("agent_base._llm")
@patch("mcp_client.list_tools")
def test_run_agent_malformed_tool_args(mock_tools, mock_llm):
    mock_tools.return_value = [{
        "name": "memclaw_write",
        "description": "Write",
        "input_schema": {"type": "object", "properties": {}},
    }]

    tc = MagicMock()
    tc.id = "call-bad"
    tc.function.name = "memclaw_write"
    tc.function.arguments = "{ this is not json }"  # malformed

    first_choice = _make_choice(tool_calls=[tc], finish_reason="tool_calls")
    second_choice = _make_choice(content="Recovered.", finish_reason="stop")

    mock_llm.return_value.chat.completions.create.side_effect = [
        _make_response(first_choice),
        _make_response(second_choice),
    ]

    result = agent_base.run_agent(
        agent_id="test-agent",
        system="sys",
        user_prompt="go",
    )
    # Should NOT crash; should record parse_error and continue
    assert result["tool_calls"][0]["status"] == "parse_error"
    assert result["final_text"] == "Recovered."


# ── run_agent — tool execution error is recorded, not raised ─────────────────

@patch("agent_base._llm")
@patch("mcp_client.call_tool", side_effect=ValueError("API error"))
@patch("mcp_client.list_tools")
def test_run_agent_tool_error_recorded(mock_tools, mock_call_tool, mock_llm):
    mock_tools.return_value = [{
        "name": "memclaw_recall",
        "description": "Recall",
        "input_schema": {"type": "object", "properties": {}},
    }]

    tc = MagicMock()
    tc.id = "call-err"
    tc.function.name = "memclaw_recall"
    tc.function.arguments = json.dumps({"query": "test"})

    first_choice = _make_choice(tool_calls=[tc], finish_reason="tool_calls")
    second_choice = _make_choice(content="Handled.", finish_reason="stop")

    mock_llm.return_value.chat.completions.create.side_effect = [
        _make_response(first_choice),
        _make_response(second_choice),
    ]

    result = agent_base.run_agent(
        agent_id="test-agent",
        system="sys",
        user_prompt="go",
    )
    assert result["tool_calls"][0]["status"] == "error"
    assert "API error" in result["tool_calls"][0]["result"]["error"]


# ── allowed_tools filtering ───────────────────────────────────────────────────

@patch("agent_base._llm")
@patch("mcp_client.list_tools")
def test_allowed_tools_filters_correctly(mock_tools, mock_llm):
    mock_tools.return_value = [
        {"name": "memclaw_write",  "description": "", "input_schema": {}},
        {"name": "memclaw_recall", "description": "", "input_schema": {}},
    ]
    mock_llm.return_value.chat.completions.create.return_value = _make_response(
        _make_choice(content="ok", finish_reason="stop")
    )
    agent_base.run_agent(
        agent_id="test-agent",
        system="sys",
        user_prompt="go",
        allowed_tools=["memclaw_recall"],  # only recall allowed
    )
    call_kwargs = mock_llm.return_value.chat.completions.create.call_args.kwargs
    tool_names = [t["function"]["name"] for t in call_kwargs.get("tools", [])]
    assert "memclaw_recall" in tool_names
    assert "memclaw_write" not in tool_names

# ── run_agent — warns when max_iterations is exhausted ──────────────────────

@patch("agent_base._llm")
@patch("mcp_client.call_tool", return_value={"ok": True})
@patch("mcp_client.list_tools")
def test_run_agent_warns_when_max_iterations_reached(
    mock_tools, mock_call_tool, mock_llm, caplog
):
    mock_tools.return_value = [{
        "name": "memclaw_recall",
        "description": "Recall",
        "input_schema": {"type": "object", "properties": {}},
    }]

    tc = MagicMock()
    tc.id = "call-forever"
    tc.function.name = "memclaw_recall"
    tc.function.arguments = json.dumps({"query": "test"})

    # Always return a tool call so the agent reaches the iteration cap.
    mock_llm.return_value.chat.completions.create.return_value = _make_response(
        _make_choice(tool_calls=[tc], finish_reason="tool_calls")
    )

    with caplog.at_level("WARNING"):
        result = agent_base.run_agent(
            agent_id="test-agent",
            system="sys",
            user_prompt="keep going",
            max_iterations=3,
        )

    assert result["iterations"] == 3
    assert len(result["tool_calls"]) == 3
    assert "hit max_iterations (3) with tool calls still pending" in caplog.text
