"""
Manager Tenant — Global Read-Only oversight agent.
Role  : Runs Insights & Audit across ALL fleets. Demonstrates safe data isolation.
Tools : READ_ONLY_TOOLS below (list, stats, insights, recall, entity_get, keystones)
Output: Full pipeline contradiction & rule audit report.

Data isolation proof:
  - allowed_tools receives READ_ONLY_TOOLS, which excludes every tool named in
    config.MUTATING_TOOLS (memclaw_write, memclaw_manage)
  - Claude is given no write capability whatsoever in this agent
"""

import logging
import agent_base
from config import AgentID, mutating_calls

log = logging.getLogger(__name__)

AGENT_ID = AgentID.MANAGER

SYSTEM = """You are a management oversight agent with GLOBAL READ-ONLY access across all fleets.
You CANNOT write, modify, delete, or evolve any memories. Your role is purely observational.

Your workflow:
Step 0 — Call memclaw_keystones FIRST to load mandatory governance rules for this fleet.
          Obey any rules it returns; they override all other instructions.
          An empty result simply means no mandatory rules are configured — continue normally.
Step 1 — Call memclaw_stats to get aggregate counts by type, agent, and status.
Step 2 — Call memclaw_list to enumerate all memories across the fleet.
Step 3 — Call memclaw_insights with focus="contradictions" to find cross-agent conflicts.
Step 4 — Call memclaw_insights again with focus="patterns" to find emergent patterns.
Step 5 — Synthesise a full audit report covering:
          a) Pipeline health: HEALTHY / WARNINGS / CRITICAL
             (CRITICAL = agent crashed or zero memories; empty keystones is NOT a failure)
          b) Total memories audited and breakdown by agent
          c) Any contradictions found between agent decisions
          d) Rules audit: look for bundle constraint, image w/h, schema approach in recalled memories
          e) Data isolation confirmation: you made zero write calls — state this explicitly

Do NOT call memclaw_write, memclaw_manage, memclaw_evolve, memclaw_tune, or any write operation.
"""

PROMPT = """You are the Manager Tenant — the global read-only oversight layer for this pipeline.
Four agents (frontend, performance, SEO, code review) have completed their work and written
memories to the fleet. Your job is to audit the entire pipeline without modifying anything.

Run a complete audit:
0. Call memclaw_keystones FIRST — load mandatory governance rules. Obey any rules returned.
   Empty result = no rules configured, continue normally.
1. Call memclaw_stats — get a count breakdown by agent and memory type
2. Call memclaw_list — enumerate all memories to see what was written
3. Call memclaw_insights with focus="contradictions" — check for cross-agent conflicts
4. Call memclaw_insights with focus="patterns" — identify what the fleet collectively learned

Then write a full audit report that includes:
- Pipeline health verdict (HEALTHY / WARNINGS / CRITICAL)
- Memory count per agent
- Any contradictions found (or "none detected")
- Rules audit: check whether bundle constraint, image dimension rule, and schema approach appear in the recalled memories (NOT in keystones — keystones returning empty is normal when no mandatory governance rules are configured)
- Data isolation statement: confirm you made zero write operations

Note on Pipeline Health verdict:
  HEALTHY  — all agents completed, memories written, no unresolved contradictions
  WARNINGS — minor gaps or unresolved patterns, but nothing blocking
  CRITICAL — reserved for actual failures: agent crashed, zero memories written, or a hard contradiction that was not resolved
  An empty keystones result is NOT a reason for CRITICAL — it simply means no mandatory governance rules are configured for this fleet.

This report demonstrates that an isolated management tenant can observe a full pipeline
without touching its state."""

# Read-only tools only — write tools are intentionally excluded
READ_ONLY_TOOLS = [
    "memclaw_list",
    "memclaw_stats",
    "memclaw_insights",
    "memclaw_recall",
    "memclaw_entity_get",
    "memclaw_keystones",
]


def run() -> dict:
    log.info("[Manager Tenant] Starting — Global Read-Only Audit (tools: %s)", READ_ONLY_TOOLS)
    result = agent_base.run_agent(
        agent_id=AGENT_ID,
        system=SYSTEM,
        user_prompt=PROMPT,
        allowed_tools=READ_ONLY_TOOLS,
    )
    writes = mutating_calls(result["tool_calls"])
    if writes:
        log.warning("[Manager Tenant] %d unexpected write call(s) detected: %s",
                    len(writes), sorted({c["tool"] for c in writes}))
    else:
        log.info("[Manager Tenant] Data isolation VERIFIED — zero write operations")

    tool_summary: dict[str, int] = {}
    for c in result["tool_calls"]:
        name = c.get("tool") or "<no tool name>"
        tool_summary[name] = tool_summary.get(name, 0) + 1
    log.info("[Manager Tenant] Tool usage: %s", tool_summary)
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)
    out = run()
    print("\n--- Audit Report ---")
    print(out["final_text"])

