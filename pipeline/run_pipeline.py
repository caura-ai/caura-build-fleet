"""
Pipeline Orchestrator — runs all 5 agents in sequence via MCP tool-use.

Each agent:
  1. Discovers MemClaw tools from the MCP server (tools/list)
    2. Sends its prompt to the configured LLM with those tools attached
    3. The model calls memclaw_recall / memclaw_write / memclaw_insights as needed
    4. The loop runs until the model stops issuing tool calls

Execution order:
  1. Frontend Agent    — write only  (nothing to recall, first in chain)
  2. Performance Agent — recall → write
  3. SEO Agent         — recall → write  (informed by Performance constraints)
  4. Code Review Agent — recall → insights → write
  5. Manager Tenant    — list + stats + insights  (read-only, no write)

Usage:
  python run_pipeline.py
  python run_pipeline.py --skip-manager
  python run_pipeline.py --dry-run
  python run_pipeline.py --json-output results.json
  python run_pipeline.py --log-level DEBUG
"""

import sys
import os
import time
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding so UTF-8 chars print correctly
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Load .env BEFORE any module that reads env vars at import time
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))

import agent_frontend
import agent_performance
import agent_seo
import agent_codereview
import manager as agent_manager
import mcp_client as mcp
from config import AgentID, mutating_calls


def _setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


log = logging.getLogger(__name__)

PIPELINE_STEPS = [
    ("Frontend Agent",     agent_frontend,    "recall:—  write:HTML5/CSS decisions"),
    ("Performance Agent",  agent_performance, "recall:frontend → write:CWV rules"),
    ("SEO Agent",          agent_seo,         "recall:all → write:SEO decisions"),
    ("Code Review Agent",  agent_codereview,  "recall:all + insights → write:verdict"),
    ("Manager Tenant",     agent_manager,     "list+stats+insights  (read-only audit)"),
]


def check_env() -> list[str]:
    return [k for k in ("LLM_GATEWAY_API_KEY", "LLM_GATEWAY_API_URL", "LLM_GATEWAY_MODEL",
                        "MEMCLAW_API_KEY", "MEMCLAW_TENANT_ID", "MEMCLAW_FLEET_ID")
            if not os.environ.get(k)]


def _mask(value: str, visible: int = 4) -> str:
    """Show only the last `visible` chars of a sensitive value."""
    if not value or value == "(not set)":
        return "(not set)"
    return f"{'*' * max(0, len(value) - visible)}{value[-visible:]}"


def print_banner():
    tenant_raw = os.environ.get("MEMCLAW_TENANT_ID", "(not set)")
    W = 65
    print()
    print("┌" + "─" * (W - 2) + "┐")
    print("│" + "  MemClaw Fleet  ·  5-Agent MCP Pipeline".center(W - 2) + "│")
    print("│" + "  Recall Before Acting  ·  Write After Deciding".center(W - 2) + "│")
    print("├" + "─" * (W - 2) + "┤")
    print(f"│  Started  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<{W - 15}}│")
    print(f"│  Fleet    : {os.environ.get('MEMCLAW_FLEET_ID', '(not set)'):<{W - 15}}│")
    print(f"│  Tenant   : {_mask(tenant_raw):<{W - 15}}│")
    print(f"│  Model    : {os.environ.get('LLM_GATEWAY_MODEL', '(not set)'):<{W - 15}}│")
    print(f"│  MCP      : {mcp.get_mcp_url():<{W - 15}}│")
    print("└" + "─" * (W - 2) + "┘")
    print()


def print_pipeline_table(steps):
    print("  Execution plan")
    print("  ┌─────┬────────────────────────┬──────────────────────────────────────┐")
    print("  │  #  │ Agent                  │ MCP Tool Usage                       │")
    print("  ├─────┼────────────────────────┼──────────────────────────────────────┤")
    for i, (name, _, desc) in enumerate(steps, 1):
        print(f"  │  {i}  │ {name:<22} │ {desc:<36} │")
    print("  └─────┴────────────────────────┴──────────────────────────────────────┘")
    print()


def _bootstrap_agent(agent_id: str, label: str) -> None:
    """Write two seed memories to register the agent identity in MemClaw.
    Trust level must be set to 2 via the admin API before running the pipeline:
    PATCH /api/agents/{agent_id}/trust  {"trust_level": 2}
    """
    written = 0
    seeds = [
        {"content": f"{label} bootstrap — agent registration (seed 1).",
         "tags": ["bootstrap", label.lower().replace(" ", "-"), "seed"]},
        {"content": f"{label} bootstrap — audit capability registration (seed 2).",
         "tags": ["bootstrap", label.lower().replace(" ", "-"), "audit"]},
    ]
    for seed in seeds:
        try:
            result = mcp.call_tool("memclaw_write", {
                "content":    seed["content"],
                "importance": 0.1,
                "tags":       seed["tags"],
            }, agent_id=agent_id)
            if isinstance(result, dict) and result.get("status") == "duplicate":
                written += 1  # already exists — counts toward trust
                log.debug("%s seed already exists (duplicate) — trust still valid.", label)
            else:
                written += 1
        except Exception as exc:
            log.warning("%s bootstrap seed failed (non-fatal): %s", label, exc)
    log.info("%s pre-registered — %d seed write(s) completed (trust_level target: 2).", label, written)


def run_pipeline(steps) -> dict:
    # Pre-register agents that need trust_level >= 2 before the pipeline starts.
    # memclaw_insights / memclaw_stats / memclaw_list require trust >= 2.
    # Trust level is set permanently via the admin API (PATCH /api/agents/{id}/trust).
    # Bootstrap writes ensure the agent identity exists in MemClaw before it runs.
    module_set = {module for _, module, _ in steps}
    if agent_manager in module_set:
        _bootstrap_agent(AgentID.MANAGER, "Manager Tenant")
    if agent_codereview in module_set:
        _bootstrap_agent(AgentID.CODE_REVIEW, "Code Review Agent")

    results = {}
    for i, (name, module, _) in enumerate(steps, 1):
        log.info("[%d/%d] Starting %s", i, len(steps), name)
        t0 = time.time()
        try:
            data = module.run()
            elapsed = time.time() - t0
            results[name] = {"status": "ok", "elapsed_s": round(elapsed, 2), "data": data}
            calls = len(data.get("tool_calls", []))
            iters = data.get("iterations", 0)
            log.info("[%d/%d] ✓ %s  %.1fs  %d tool call(s)  %d iteration(s)",
                     i, len(steps), name, elapsed, calls, iters)
        except Exception as exc:
            elapsed = time.time() - t0
            results[name] = {"status": "error", "elapsed_s": round(elapsed, 2), "error": str(exc)}
            log.error("[%d/%d] ✗ %s  FAILED: %s", i, len(steps), name, exc, exc_info=True)
    return results


_HEALTH_SYNONYMS: dict[str, list[str]] = {
    "HEALTHY":  ["ALL GOOD", "NO ISSUES", "OPERATING WELL", "OPERATING HEALTHILY",
                 "OPERATING CORRECTLY", "COMPLETED SUCCESSFULLY", "NO CONTRADICTIONS",
                 "NO CONFLICTS", "GREEN"],
    "WARNINGS": ["MINOR ISSUES", "MINOR GAPS", "SOME GAPS", "UNRESOLVED PATTERNS",
                 "PARTIAL", "INCOMPLETE"],
    "CRITICAL": ["CRITICAL FAILURE", "HARD CONTRADICTION", "ZERO MEMORIES",
                 "AGENT CRASHED", "FAILED TO COMPLETE"],
}


def _parse_verdict(text: str, keywords: list[str], fallback: str = "?") -> str:
    """Return the verdict whose keyword or synonym appears first in the text."""
    if not text:
        return fallback
    upper = str(text).upper()
    best: tuple[int, str] | None = None
    for kw in keywords:
        for needle in [kw, *_HEALTH_SYNONYMS.get(kw, [])]:
            position = upper.find(needle.upper())
            if position != -1 and (best is None or position < best[0]):
                best = (position, kw)
    return best[1] if best else fallback


def print_summary(results: dict):
    print("\n\n" + "═" * 65)
    print("  ✦  RUN COMPLETE")
    print("═" * 65)

    # Agent timing table
    print()
    print("  Agent Results")
    print("  ┌────────────────────────┬────────┬──────────────────────────────────┐")
    print("  │ Agent                  │  Time  │ Tool Calls                       │")
    print("  ├────────────────────────┼────────┼──────────────────────────────────┤")
    for name, r in results.items():
        t = r["elapsed_s"]
        if r["status"] == "ok":
            data = r["data"]
            by_tool: dict[str, int] = {}
            for c in data.get("tool_calls", []):
                by_tool[c["tool"]] = by_tool.get(c["tool"], 0) + 1
            # shorten tool names for compact display
            tool_str = "  ".join(
                f"{k.replace('memclaw_', '')}×{v}" for k, v in by_tool.items()
            ) or "—"
            status_icon = "✓"
            print(f"  │ {status_icon} {name:<21} │ {t:>5.1f}s │ {tool_str:<32} │")
        else:
            err = r["error"][:30]
            print(f"  │ ✗ {name:<21} │ {t:>5.1f}s │ ERROR: {err:<25} │")
    print("  └────────────────────────┴────────┴──────────────────────────────────┘")

    # Verdict block — no right-border on emoji rows (emoji are double-width in terminals)
    print()
    print("  Verdicts")
    print("  " + "─" * 63)

    cr = results.get("Code Review Agent", {})
    if cr.get("status") == "ok":
        text = cr["data"].get("final_text", "")
        verdict = _parse_verdict(text, ["LGTM", "BLOCK"])
        if verdict == "?":
            # Model wrote the verdict into a memory rather than its final text — scan tool results
            for tc in cr["data"].get("tool_calls", []):
                if tc.get("tool") == "memclaw_write" and tc.get("status") == "ok":
                    result_content = json.dumps(tc.get("result", ""))
                    input_content = json.dumps(tc.get("input", ""))
                    for src in (input_content, result_content):
                        found = _parse_verdict(src, ["LGTM", "BLOCK"])
                        if found != "?":
                            verdict = found
                            break
                if verdict != "?":
                    break
        icon = "✅" if verdict == "LGTM" else ("🚫" if verdict == "BLOCK" else "❓")
        print(f"  Code Review Verdict  :  {icon}  {verdict}")

    mgr = results.get("Manager Tenant", {})
    if mgr.get("status") == "ok":
        text = mgr["data"].get("final_text", "")
        health = _parse_verdict(text, ["HEALTHY", "WARNINGS", "CRITICAL"])
        if health == "?":
            # Model may have embedded the verdict in a tool input or result — scan all calls
            for tc in mgr["data"].get("tool_calls", []):
                for src in (json.dumps(tc.get("input", "")), json.dumps(tc.get("result", ""))):
                    found = _parse_verdict(src, ["HEALTHY", "WARNINGS", "CRITICAL"])
                    if found != "?":
                        health = found
                        break
                if health != "?":
                    break
        h_icon = "✅" if health == "HEALTHY" else ("⚠️" if health == "WARNINGS" else ("🚫" if health == "CRITICAL" else "❓"))
        print(f"  Pipeline Health      :  {h_icon}  {health}")

        mgr_calls = mgr["data"].get("tool_calls", [])
        write_calls = mutating_calls(mgr_calls)
        def _is_successful_read(c: dict) -> bool:
            if c.get("tool") not in {"memclaw_list", "memclaw_stats", "memclaw_recall", "memclaw_entity_get", "memclaw_keystones"}:
                return False
            if c.get("status") != "ok":
                return False
            return True

        def _insights_ok(c: dict) -> bool:
            """memclaw_insights returns HTTP 200 even on auth/trust errors — check the body."""
            if c["tool"] != "memclaw_insights" or c.get("status") != "ok":
                return False
            result = c.get("result", {})
            if isinstance(result, dict):
                # Error bodies have "error" key or "detail" containing failure messages
                err = result.get("error") or result.get("detail") or ""
                if err and ("not registered" in str(err).lower() or "403" in str(err) or "trust" in str(err).lower()):
                    return False
            return True

        read_ok = any(_is_successful_read(c) or _insights_ok(c) for c in mgr_calls)
        if not write_calls and read_ok:
            iso_icon, iso_text = "✅", "VERIFIED  (zero writes, reads ok)"
        elif not write_calls and not read_ok:
            iso_icon, iso_text = "⚠️", "UNCONFIRMED  (reads failed — check trust level)"
        else:
            iso_icon, iso_text = "🚫", "FAILED  (unexpected writes detected)"
        print(f"  Data Isolation       :  {iso_icon}  {iso_text}")

    print("  " + "─" * 63)
    print()
    print(f"  ▸ View memories : {mcp.get_base_url().rstrip('/')}/prism")
    print("═" * 65 + "\n")


def reset_fleet_memories() -> None:
    """Delete all memories in the current fleet after a run."""
    fleet_id = os.environ.get("MEMCLAW_FLEET_ID", "fleet")
    log.info("Resetting fleet memories for fleet_id=%r …", fleet_id)
    try:
        result = mcp.call_tool("memclaw_list", {"fleet_id": fleet_id}, agent_id=AgentID.MANAGER)
        memories = result.get("items") or result.get("memories") or result.get("results") or []
        if not memories:
            log.info("No memories found to delete.")
            return
        deleted = 0
        failed = 0
        for mem in memories:
            mid = mem.get("id")
            if not mid:
                continue
            try:
                mcp.call_tool("memclaw_manage", {"op": "delete", "memory_id": mid}, agent_id=AgentID.MANAGER)
                deleted += 1
            except Exception as exc:
                log.warning("Failed to delete memory %s: %s", mid, exc)
                failed += 1
        log.info("Fleet reset complete — deleted %d, failed %d.", deleted, failed)
        print(f"\n  Fleet Reset         : {deleted} memories deleted, {failed} failed.")
    except Exception as exc:
        log.error("Fleet reset failed: %s", exc)
        print(f"\n  Fleet Reset         : ⚠️  FAILED — {exc}")


def exit_code(results: dict) -> int:
    """Return a non-zero exit code when any pipeline step failed."""
    return int(any(result["status"] == "error" for result in results.values()))


def validate_json_output_path(path_str: str) -> Path:
    """Ensure --json-output parent dir exists and is writable before the run."""
    path = Path(path_str)
    parent = path.parent if str(path.parent) not in ("", ".") else Path(".")
    parent = parent.resolve()
    if not parent.exists():
        raise SystemExit(
            f"--json-output parent directory does not exist: {parent}"
        )
    if not parent.is_dir():
        raise SystemExit(
            f"--json-output parent path is not a directory: {parent}"
        )
    probe = parent / (".caura_write_probe_" + str(os.getpid()))
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise SystemExit(
            f"--json-output directory is not writable: {parent} ({exc})"
        ) from exc
    return path

def main():
    parser = argparse.ArgumentParser(description="MemClaw 5-Fleet SaaS Build Pipeline")
    parser.add_argument("--dry-run",      action="store_true", help="Check env + MCP connectivity, then exit")
    parser.add_argument("--skip-manager", action="store_true", help="Skip Manager Tenant audit")
    parser.add_argument("--reset",        action="store_true", help="Delete all fleet memories after the run completes")
    parser.add_argument("--loop",         action="store_true", help="Re-run pipeline after each run; resets memories between iterations, pauses for keypress")
    parser.add_argument("--json-output",  metavar="FILE",      help="Write full results to JSON file")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    args = parser.parse_args()

    _setup_logging(args.log_level)
    if args.json_output:
        validate_json_output_path(args.json_output)
    print_banner()

    missing = check_env()
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        print("Copy .env.example → .env and fill in your keys.")
        sys.exit(1)

    steps = PIPELINE_STEPS[:-1] if args.skip_manager else PIPELINE_STEPS

    if args.dry_run:
        log.info("Checking MemClaw API connectivity...")
        try:
            ok = mcp.health_check()
            if ok:
                tools = mcp.list_tools()
                log.info("MemClaw API reachable — %d tools defined: %s",
                         len(tools), [t["name"] for t in tools])
            else:
                log.error("MemClaw API returned an error — check MEMCLAW_API_KEY and MEMCLAW_TENANT_ID")
                sys.exit(1)
        except Exception as exc:
            log.error("MemClaw connection failed: %s", exc, exc_info=True)
            sys.exit(1)
        log.info("Dry run complete — env OK, MemClaw OK.")
        print_pipeline_table(steps)
        sys.exit(0)

    print_pipeline_table(steps)

    run_number = 0
    pipeline_exit_code = 0
    while True:
        run_number += 1
        if args.loop and run_number > 1:
            print(f"\n  ── Loop iteration {run_number} ──\n")

        results = run_pipeline(steps)
        pipeline_exit_code = max(pipeline_exit_code, exit_code(results))
        print_summary(results)

        if args.reset or args.loop:
            reset_fleet_memories()

        if args.json_output:
            safe = json.loads(json.dumps(results, default=str))
            Path(args.json_output).write_text(json.dumps(safe, indent=2), encoding="utf-8")
            log.info("Full results written to %s", args.json_output)

        if not args.loop:
            break

        print("\n  Press Enter to run again, or Ctrl+C to exit…")
        try:
            input()
        except KeyboardInterrupt:
            print("\n  Stopped.")
            break

    sys.exit(pipeline_exit_code)


if __name__ == "__main__":
    main()
