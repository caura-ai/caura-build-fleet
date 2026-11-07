"""Entry points use .env defaults without replacing exported configuration."""

import os
import runpy
import sys
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_DIR))

import agent_base
import mcp_client


@pytest.mark.parametrize("entrypoint", [
    "run_pipeline.py",
    "agent_frontend.py",
    "agent_performance.py",
    "agent_seo.py",
    "agent_codereview.py",
    "manager.py",
])
@pytest.mark.parametrize("exported", [True, False], ids=["exported", "dotenv-only"])
def test_entrypoint_dotenv_precedence(entrypoint, exported, tmp_path, monkeypatch):
    script = tmp_path / entrypoint
    script.write_text((PIPELINE_DIR / entrypoint).read_text(encoding="utf-8"), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MEMCLAW_FLEET_ID=dotenv-fleet\nLLM_GATEWAY_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    # A copy next to the fixture exercises real .env discovery without touching
    # the developer's .env. Restore variables added by python-dotenv as well.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    for key in ("MEMCLAW_API_KEY", "MEMCLAW_TENANT_ID", "LLM_GATEWAY_API_KEY"):
        monkeypatch.setenv(key, "test-value")
    monkeypatch.setenv("LLM_GATEWAY_API_URL", "https://example.com/v1")
    for key, value in (
        ("MEMCLAW_FLEET_ID", "shell-fleet"),
        ("LLM_GATEWAY_MODEL", "shell-model"),
    ):
        if exported:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)

    observed = []

    def record_config():
        observed.append((os.getenv("MEMCLAW_FLEET_ID"), os.getenv("LLM_GATEWAY_MODEL")))

    def run_agent(**kwargs):
        record_config()
        return {"final_text": "LGTM", "tool_calls": [], "iterations": 1}

    def health_check():
        record_config()
        return True

    # No LLM or MCP network calls: only the entry-point configuration is under test.
    monkeypatch.setattr(agent_base, "run_agent", run_agent)
    monkeypatch.setattr(mcp_client, "health_check", health_check)
    monkeypatch.setattr(mcp_client, "list_tools", lambda: [])
    monkeypatch.syspath_prepend(str(PIPELINE_DIR))
    monkeypatch.setattr(sys, "argv", [entrypoint, "--dry-run"])

    if entrypoint == "run_pipeline.py":
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(str(script), run_name="__main__")
        assert exc_info.value.code == 0
    else:
        runpy.run_path(str(script), run_name="__main__")

    prefix = "shell" if exported else "dotenv"
    assert observed == [(f"{prefix}-fleet", f"{prefix}-model")]
