"""Tests for pipeline verdict parsing."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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


def test_banner_bounds_long_values(capsys, monkeypatch):
    import run_pipeline as pipeline
    for key in ('MEMCLAW_FLEET_ID', 'MEMCLAW_TENANT_ID', 'LLM_GATEWAY_MODEL'):
        monkeypatch.setenv(key, 'x' * 100)
    monkeypatch.setattr(pipeline.mcp, 'get_mcp_url', lambda: 'https://example.com/' + 'x' * 100)
    pipeline.print_banner()
    lines = capsys.readouterr().out.strip().splitlines()
    assert {len(line) for line in lines} == {65}
    assert sum('…' in line for line in lines) == 4


def test_banner_preserves_short_values(capsys, monkeypatch):
    import run_pipeline as pipeline
    monkeypatch.setenv('LLM_GATEWAY_MODEL', 'test-model')
    pipeline.print_banner()
    output = capsys.readouterr().out
    assert 'test-model' in output
    assert {len(line) for line in output.strip().splitlines()} == {65}


def test_pipeline_table_expands_columns(capsys):
    import run_pipeline as pipeline
    steps = [("A" * 40, None, "D" * 80)] * 12
    pipeline.print_pipeline_table(steps)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith('  ') and line[2:3] in '┌│├└']
    assert len({len(line) for line in lines}) == 1
    assert 'A' * 40 in lines[3]
    assert 'D' * 80 in lines[3]
    assert '12' in lines[-2]


def test_pipeline_table_empty_and_default(capsys):
    import run_pipeline as pipeline
    for steps in ([], pipeline.PIPELINE_STEPS):
        pipeline.print_pipeline_table(steps)
        lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith('  ') and line[2:3] in '┌│├└']
        assert len({len(line) for line in lines}) == 1
