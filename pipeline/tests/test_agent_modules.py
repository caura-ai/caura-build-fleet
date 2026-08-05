"""Unit tests for thin agent wrappers and manager isolation (#11, #12)."""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("MEMCLAW_API_KEY", "test-key")
os.environ.setdefault("MEMCLAW_TENANT_ID", "test-tenant")
os.environ.setdefault("MEMCLAW_FLEET_ID", "test-fleet")
os.environ.setdefault("LLM_GATEWAY_API_KEY", "test-llm-key")

import agent_codereview
import agent_frontend
import agent_performance
import agent_seo
import manager
from config import AgentID


def _fake_result(tool_calls=None, final_text="ok"):
    return {
        "tool_calls": tool_calls or [],
        "final_text": final_text,
    }


@pytest.mark.parametrize(
    "mod,agent_id,allowed",
    [
        (agent_frontend, AgentID.FRONTEND, ["memclaw_write"]),
        (agent_performance, AgentID.PERFORMANCE, ["memclaw_recall", "memclaw_write"]),
        (agent_seo, AgentID.SEO, ["memclaw_recall", "memclaw_write"]),
    ],
)
def test_agent_module_wires_id_and_tools(mod, agent_id, allowed):
    assert mod.AGENT_ID == agent_id
    assert mod.ALLOWED_TOOLS == allowed
    with patch("agent_base.run_agent", return_value=_fake_result()) as mock_run:
        out = mod.run()
    assert out["final_text"] == "ok"
    kwargs = mock_run.call_args.kwargs
    assert kwargs["agent_id"] == agent_id
    assert kwargs["allowed_tools"] == allowed
    assert "system" in kwargs and kwargs["system"]
    assert "user_prompt" in kwargs and kwargs["user_prompt"]


def test_manager_read_only_tools_and_isolation_flags_writes(caplog):
    assert "memclaw_write" not in manager.READ_ONLY_TOOLS
    assert "memclaw_manage" not in manager.READ_ONLY_TOOLS
    bad = _fake_result(
        tool_calls=[
            {"tool": "memclaw_list", "args": {}},
            {"tool": "memclaw_write", "args": {"content": "nope"}},
        ]
    )
    with patch("agent_base.run_agent", return_value=bad) as mock_run:
        with caplog.at_level("WARNING"):
            manager.run()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["agent_id"] == AgentID.MANAGER
    assert kwargs["allowed_tools"] == manager.READ_ONLY_TOOLS
    assert any("unexpected write" in r.message.lower() for r in caplog.records)


def test_manager_isolation_verified_when_no_writes(caplog):
    good = _fake_result(tool_calls=[{"tool": "memclaw_list", "args": {}}])
    with patch("agent_base.run_agent", return_value=good):
        with caplog.at_level("INFO"):
            manager.run()
    assert any("data isolation verified" in r.message.lower() for r in caplog.records)


def test_codereview_verdict_line_extraction(caplog):
    text = "Summary of review\nLGTM — all agents consistent\nThanks"
    result = _fake_result(
        tool_calls=[{"tool": "memclaw_recall", "args": {}}],
        final_text=text,
    )
    with patch("agent_base.run_agent", return_value=result):
        with caplog.at_level("INFO"):
            out = agent_codereview.run()
    assert out["final_text"] == text
    assert any("LGTM" in r.message for r in caplog.records)
