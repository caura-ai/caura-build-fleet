"""
Unit tests for the Manager data-isolation check.

The Manager is the agent the README advertises as read-only, and two separate
places decide whether it wrote: manager.py (its own report) and run_pipeline.py
(the orchestrator's isolation summary). Both must agree on what "a write" is, so
the predicate lives in config.py and these tests pin it down.

Run:  cd pipeline && pytest tests/ -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("MEMCLAW_API_KEY", "test-key")
os.environ.setdefault("MEMCLAW_TENANT_ID", "test-tenant")
os.environ.setdefault("MEMCLAW_FLEET_ID", "test-fleet")
os.environ.setdefault("LLM_GATEWAY_API_KEY", "test-llm-key")

import config
from config import MUTATING_TOOLS, is_mutating_tool, mutating_calls


# ── the set itself ────────────────────────────────────────────────────────────

def test_mutating_tools_only_names_tools_that_exist():
    """Every name must be a real, dispatchable tool.

    Guards against the failure mode that started this: manager.py's docstring
    listed memclaw_doc and memclaw_keystones_set, which no code defines.
    """
    import mcp_client

    known = set()
    source = None
    try:
        import inspect
        source = inspect.getsource(mcp_client.call_tool)
    except (OSError, TypeError):  # pragma: no cover - source always available here
        pass

    assert source is not None
    for tool in MUTATING_TOOLS:
        assert f'"{tool}"' in source, (
            f"MUTATING_TOOLS lists {tool!r}, which call_tool() never dispatches"
        )


def test_read_only_tools_are_not_mutating():
    """The Manager's allowlist must not contain any mutating tool."""
    import manager

    overlap = set(manager.READ_ONLY_TOOLS) & MUTATING_TOOLS
    assert not overlap, f"READ_ONLY_TOOLS grants mutating tools: {sorted(overlap)}"


def test_keystones_is_not_treated_as_mutating():
    """memclaw_keystones reads governance rules (GET); it is legitimately read-only."""
    assert not is_mutating_tool("memclaw_keystones")


# ── the predicate ─────────────────────────────────────────────────────────────

def test_is_mutating_tool_accepts_known_write_tools():
    assert is_mutating_tool("memclaw_write")
    assert is_mutating_tool("memclaw_manage")


def test_is_mutating_tool_rejects_read_tools():
    for name in ("memclaw_list", "memclaw_stats", "memclaw_recall", "memclaw_insights"):
        assert not is_mutating_tool(name)


def test_is_mutating_tool_matches_names_not_substrings():
    """The old heuristic matched 'write'/'manage' anywhere in the name.

    A tool named for what it does to memory must be recognised by membership, not
    by whether its name happens to contain a keyword.
    """
    # Names containing the old keywords but not actually mutating tools.
    assert not is_mutating_tool("memclaw_writer_stats")
    # A future mutating tool whose name contains neither keyword is still only
    # caught if it is added to the set -- pin that the set is the single source.
    assert not is_mutating_tool("memclaw_forget")
    assert is_mutating_tool("memclaw_manage")


def test_is_mutating_tool_is_safe_for_missing_and_non_string_names():
    """Log entries may lack a tool name; the orchestrator copy already guarded for
    this and the Manager copy would have raised KeyError. One predicate now."""
    assert not is_mutating_tool(None)
    assert not is_mutating_tool("")
    assert not is_mutating_tool(123)
    assert not is_mutating_tool(["memclaw_write"])


# ── mutating_calls ────────────────────────────────────────────────────────────

def test_mutating_calls_returns_only_writes():
    calls = [
        {"tool": "memclaw_list", "status": "ok"},
        {"tool": "memclaw_write", "status": "ok"},
        {"tool": "memclaw_insights", "status": "ok"},
        {"tool": "memclaw_manage", "status": "ok"},
    ]
    found = mutating_calls(calls)
    assert [c["tool"] for c in found] == ["memclaw_write", "memclaw_manage"]


def test_mutating_calls_handles_entries_without_a_tool_key():
    """The exact shape that made the two copies disagree."""
    calls = [{"status": "ok"}, {"tool": "memclaw_write"}]
    assert [c["tool"] for c in mutating_calls(calls)] == ["memclaw_write"]


def test_mutating_calls_on_a_clean_run_is_empty():
    calls = [
        {"tool": "memclaw_keystones", "status": "ok"},
        {"tool": "memclaw_stats", "status": "ok"},
        {"tool": "memclaw_list", "status": "ok"},
    ]
    assert mutating_calls(calls) == []


def test_mutating_calls_on_empty_input():
    assert mutating_calls([]) == []


# ── manager.run() actually reports through the shared predicate ───────────────

def _run_manager_with_calls(tool_calls, caplog):
    from unittest.mock import patch

    import manager

    with patch("agent_base.run_agent", return_value={
        "agent_id": "manager-tenant",
        "final_text": "audit",
        "tool_calls": tool_calls,
        "iterations": 1,
    }):
        with caplog.at_level("INFO"):
            return manager.run()


def test_manager_run_reports_verified_on_a_clean_audit(caplog):
    result = _run_manager_with_calls(
        [
            {"tool": "memclaw_keystones", "status": "ok"},
            {"tool": "memclaw_stats", "status": "ok"},
            {"tool": "memclaw_list", "status": "ok"},
        ],
        caplog,
    )
    assert "Data isolation VERIFIED" in caplog.text
    assert result["tool_calls"][1]["tool"] == "memclaw_stats"


def test_manager_run_warns_when_a_write_appears(caplog):
    _run_manager_with_calls(
        [
            {"tool": "memclaw_list", "status": "ok"},
            {"tool": "memclaw_manage", "op": "delete", "status": "ok"},
        ],
        caplog,
    )
    assert "unexpected write call(s) detected" in caplog.text
    assert "Data isolation VERIFIED" not in caplog.text
    assert "memclaw_manage" in caplog.text


def test_manager_run_survives_a_log_entry_without_a_tool_key(caplog):
    """The old manager.py copy indexed c["tool"] and raised KeyError here."""
    _run_manager_with_calls(
        [
            {"tool": "memclaw_list", "status": "ok"},
            {"status": "ok"},
        ],
        caplog,
    )
    assert "Data isolation VERIFIED" in caplog.text
