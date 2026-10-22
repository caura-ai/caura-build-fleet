"""Verify the fleet-teardown identity for issue #33.

Why this is a script and not a pytest file: `run_pipeline` wraps `sys.stdout` at
import time (`_setup_logging`). Under pytest that breaks the capture machinery, so
pytest cannot report results for any module importing it -- on this repo
`tests/test_run_pipeline.py` fails the same way on master (`ValueError: I/O
operation on closed file`). A pytest file here would be unrunnable and would look
like a passing suite while asserting nothing.

This script applies the same checks directly, prints a result per check on stderr
(before stdout wrapping can swallow anything), and exits non-zero on failure.

    python tests/verify_teardown_identity.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("MEMCLAW_API_KEY", "test-key")
os.environ.setdefault("MEMCLAW_TENANT_ID", "test-tenant")
os.environ.setdefault("MEMCLAW_FLEET_ID", "test-fleet")
os.environ.setdefault("LLM_GATEWAY_API_KEY", "test-llm-key")

from unittest.mock import patch  # noqa: E402

import config  # noqa: E402
import run_pipeline  # noqa: E402

OUT = sys.stderr  # stdout gets wrapped by run_pipeline on import


class _Recorder:
    """Stands in for mcp.call_tool, recording (tool, agent_id) pairs."""

    def __init__(self, list_payload):
        self.list_payload = list_payload
        self.calls = []

    def __call__(self, tool_name, arguments, agent_id=None):
        self.calls.append((tool_name, agent_id))
        if tool_name == "memclaw_list":
            return self.list_payload
        return {"ok": True}

    @property
    def tools(self):
        return [t for t, _ in self.calls]

    @property
    def identities(self):
        return {a for _, a in self.calls}


def teardown_with(recorder):
    """Run reset_fleet_memories with mcp.call_tool stubbed.

    Registration is no longer part of teardown (it moved to run_pipeline's
    pre-flight), so this records mcp calls only.
    """
    with patch("mcp_client.call_tool", side_effect=recorder):
        run_pipeline.reset_fleet_memories()
    return recorder


def pipeline_with_bootstrap(modules):
    """Run run_pipeline() over fake steps, returning the bootstrap mock.

    Steps are (name, module, _) triples; each fake module only needs a `run()`
    returning a tool_calls list, so the pipeline body is exercised without any
    network work.
    """
    steps = [(f"step-{i}", m, None) for i, m in enumerate(modules)]
    with patch.object(run_pipeline, "_bootstrap_agent") as bootstrap:
        run_pipeline.run_pipeline(steps)
    return bootstrap


class _FakeAgent:
    """Minimal stand-in for an agent module inside run_pipeline()."""

    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run(self) -> dict:
        return {
            "agent_id": "fake",
            "final_text": "",
            "tool_calls": self.tool_calls,
            "iterations": 1,
        }


# ── checks ────────────────────────────────────────────────────────────────────

def check_orchestrator_identity_is_distinct():
    assert config.AgentID.ORCHESTRATOR == "orchestrator", config.AgentID.ORCHESTRATOR
    assert config.AgentID.ORCHESTRATOR != config.AgentID.MANAGER


def check_orchestrator_is_not_a_pipeline_agent():
    pipeline_ids = {
        config.AgentID.FRONTEND,
        config.AgentID.PERFORMANCE,
        config.AgentID.SEO,
        config.AgentID.CODE_REVIEW,
        config.AgentID.MANAGER,
    }
    assert config.AgentID.ORCHESTRATOR not in pipeline_ids


def check_teardown_deletes_as_orchestrator_not_manager():
    rec = _Recorder({"items": [{"id": "mem-1"}, {"id": "mem-2"}]})
    teardown_with(rec)
    assert rec.calls, "teardown issued no calls at all"
    assert rec.identities == {config.AgentID.ORCHESTRATOR}, f"identities={rec.identities}"
    assert config.AgentID.MANAGER not in rec.identities, \
        "the read-only Manager identity still appears in the teardown path"


def check_list_and_delete_use_the_same_identity():
    rec = _Recorder({"items": [{"id": "mem-1"}]})
    teardown_with(rec)
    assert rec.tools == ["memclaw_list", "memclaw_manage"], rec.tools
    assert rec.identities == {config.AgentID.ORCHESTRATOR}, rec.identities


def check_orchestrator_is_registered_by_the_run_path():
    """Registration belongs to run_pipeline's pre-flight, not to teardown.

    It runs before the pipeline rather than inside teardown, so --skip-manager is
    still satisfied (the orchestrator bootstrap is unconditional, unlike the
    Manager's) and teardown never writes into the fleet it is emptying.
    """
    bootstrap = pipeline_with_bootstrap([_FakeAgent()])
    registered = [c.args[0] for c in bootstrap.call_args_list]
    assert config.AgentID.ORCHESTRATOR in registered, registered


def check_orchestrator_registration_is_unconditional():
    """It must not be gated on which agents are in the run."""
    bootstrap = pipeline_with_bootstrap([])  # no steps at all
    registered = [c.args[0] for c in bootstrap.call_args_list]
    assert registered == [config.AgentID.ORCHESTRATOR], registered


def check_teardown_performs_no_registration():
    """The bug this guards: teardown registering by writing into the fleet."""
    with patch.object(run_pipeline, "_bootstrap_agent") as bootstrap:
        teardown_with(_Recorder({"items": [{"id": "mem-1"}]}))
    bootstrap.assert_not_called()


def check_no_memories_still_uses_orchestrator():
    rec = _Recorder({"items": []})
    teardown_with(rec)
    assert rec.tools == ["memclaw_list"], rec.tools
    assert rec.identities == {config.AgentID.ORCHESTRATOR}, rec.identities


def check_alternate_list_result_keys():
    for key in ("items", "memories", "results"):
        rec = _Recorder({key: [{"id": "m"}]})
        teardown_with(rec)
        assert rec.tools == ["memclaw_list", "memclaw_manage"], (key, rec.tools)
        assert rec.identities == {config.AgentID.ORCHESTRATOR}, (key, rec.identities)


def check_entries_without_an_id_are_skipped():
    rec = _Recorder({"items": [{"id": "m1"}, {"no_id": True}]})
    teardown_with(rec)
    assert rec.tools == ["memclaw_list", "memclaw_manage"], rec.tools


def check_a_failed_delete_does_not_crash():
    class FailOneDelete(_Recorder):
        def __call__(self, tool_name, arguments, agent_id=None):
            self.calls.append((tool_name, agent_id))
            if tool_name == "memclaw_list":
                return {"items": [{"id": "m1"}, {"id": "m2"}]}
            if arguments.get("memory_id") == "m2":
                raise RuntimeError("boom")
            return {"ok": True}

    rec = FailOneDelete(None)
    teardown_with(rec)
    assert rec.identities == {config.AgentID.ORCHESTRATOR}, rec.identities
    assert len([t for t in rec.tools if t == "memclaw_manage"]) == 2, rec.tools


def check_a_failed_list_does_not_propagate():
    """A 403 on list must not escape --reset and kill the run."""
    class FailingList(_Recorder):
        def __call__(self, tool_name, arguments, agent_id=None):
            self.calls.append((tool_name, agent_id))
            raise RuntimeError("403 trust too low")

    rec = FailingList(None)
    teardown_with(rec)
    assert rec.identities == {config.AgentID.ORCHESTRATOR}, rec.identities


CHECKS = [
    check_orchestrator_identity_is_distinct,
    check_orchestrator_is_not_a_pipeline_agent,
    check_teardown_deletes_as_orchestrator_not_manager,
    check_list_and_delete_use_the_same_identity,
    check_orchestrator_is_registered_by_the_run_path,
    check_orchestrator_registration_is_unconditional,
    check_teardown_performs_no_registration,
    check_no_memories_still_uses_orchestrator,
    check_alternate_list_result_keys,
    check_entries_without_an_id_are_skipped,
    check_a_failed_delete_does_not_crash,
    check_a_failed_list_does_not_propagate,
]


def main() -> int:
    failures = []
    for fn in CHECKS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"FAIL  {fn.__name__}: {exc}", file=OUT)
        else:
            print(f"ok    {fn.__name__}", file=OUT)

    print("", file=OUT)
    if failures:
        print(f"{len(failures)} of {len(CHECKS)} checks FAILED", file=OUT)
        return 1
    print(f"all {len(CHECKS)} checks passed", file=OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
