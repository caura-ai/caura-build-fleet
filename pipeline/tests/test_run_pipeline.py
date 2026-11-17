"""Tests for pipeline verdict parsing and agent bootstrapping."""

import os
import sys
from unittest.mock import Mock, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import run_pipeline
from run_pipeline import _parse_verdict


def test_parse_verdict_uses_first_keyword_in_text():
    assert _parse_verdict("LGTM — BLOCK is not warranted.", ["LGTM", "BLOCK"]) == "LGTM"
    assert _parse_verdict("BLOCK — this is not an LGTM.", ["LGTM", "BLOCK"]) == "BLOCK"


def test_parse_verdict_uses_first_health_synonym_in_text():
    assert _parse_verdict("All good — no CRITICAL failures.", ["HEALTHY", "WARNINGS", "CRITICAL"]) == "HEALTHY"
    assert _parse_verdict("CRITICAL: not operating healthily.", ["HEALTHY", "WARNINGS", "CRITICAL"]) == "CRITICAL"


def test_parse_verdict_is_case_insensitive_and_honours_fallback():
    assert _parse_verdict("warnings: partial output", ["HEALTHY", "WARNINGS"]) == "WARNINGS"
    assert _parse_verdict("No verdict here", ["LGTM", "BLOCK"], fallback="UNKNOWN") == "UNKNOWN"


@pytest.mark.parametrize(
    "results,written,existing,failed",
    [
        ([{"status": "created"}, {"status": "created"}], 2, 0, 0),
        ([{"status": "duplicate"}, {"status": "duplicate"}], 0, 2, 0),
        ([{"status": "created"}, {"status": "duplicate"}], 1, 1, 0),
        ([RuntimeError("offline failure"), {"status": "duplicate"}], 0, 1, 1),
        ([RuntimeError("offline failure"), {"status": "created"}], 1, 0, 1),
        ([RuntimeError("offline failure"), RuntimeError("offline failure")], 0, 0, 2),
        ([None, "ok"], 2, 0, 0),
    ],
)
def test_bootstrap_agent_reports_new_and_existing_seeds(
    monkeypatch, caplog, results, written, existing, failed
):
    call_tool = Mock(side_effect=results)
    monkeypatch.setattr(run_pipeline.mcp, "call_tool", call_tool)

    with caplog.at_level("DEBUG", logger=run_pipeline.__name__):
        assert run_pipeline._bootstrap_agent("test-agent", "Test Agent") is None

    assert call_tool.call_args_list == [
        call("memclaw_write", {
            "content": "Test Agent bootstrap — agent registration (seed 1).",
            "importance": 0.1,
            "tags": ["bootstrap", "test-agent", "seed"],
        }, agent_id="test-agent"),
        call("memclaw_write", {
            "content": "Test Agent bootstrap — audit capability registration (seed 2).",
            "importance": 0.1,
            "tags": ["bootstrap", "test-agent", "audit"],
        }, agent_id="test-agent"),
    ]
    assert caplog.messages[-1] == (
        f"Test Agent pre-registered — {written} seed write(s) completed, "
        f"{existing} seed(s) already existed (trust_level target: 2)."
    )
    assert caplog.messages.count(
        "Test Agent seed already exists (duplicate) — trust still valid."
    ) == existing
    assert caplog.messages.count(
        "Test Agent bootstrap seed failed (non-fatal): offline failure"
    ) == failed
