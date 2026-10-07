"""Tests for pipeline verdict parsing."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from run_pipeline import PIPELINE_STEPS, _parse_verdict, _select_pipeline_steps


def test_parse_verdict_uses_first_keyword_in_text():
    assert _parse_verdict("LGTM — BLOCK is not warranted.", ["LGTM", "BLOCK"]) == "LGTM"
    assert _parse_verdict("BLOCK — this is not an LGTM.", ["LGTM", "BLOCK"]) == "BLOCK"


def test_parse_verdict_uses_first_health_synonym_in_text():
    assert _parse_verdict("All good — no CRITICAL failures.", ["HEALTHY", "WARNINGS", "CRITICAL"]) == "HEALTHY"
    assert _parse_verdict("CRITICAL: not operating healthily.", ["HEALTHY", "WARNINGS", "CRITICAL"]) == "CRITICAL"


def test_parse_verdict_is_case_insensitive_and_honours_fallback():
    assert _parse_verdict("warnings: partial output", ["HEALTHY", "WARNINGS"]) == "WARNINGS"
    assert _parse_verdict("No verdict here", ["LGTM", "BLOCK"], fallback="UNKNOWN") == "UNKNOWN"


def test_skip_manager_filters_manager_by_identity_not_position():
    manager_step = PIPELINE_STEPS[-1]
    appended_step = ("Late Agent", object(), "runs after manager")
    steps = [*PIPELINE_STEPS, appended_step]

    selected = _select_pipeline_steps(steps, skip_manager=True)

    assert manager_step not in selected
    assert appended_step in selected
    assert selected == [*PIPELINE_STEPS[:-1], appended_step]
