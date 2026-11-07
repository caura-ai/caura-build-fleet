"""
Fleet 4 — Code Review Agent
Role  : Issue LGTM / BLOCK verdict with citations to specific memory IDs.
Tools : memclaw_recall → memclaw_write (draft) → memclaw_insights → memclaw_write (verdict)
Writes: Final decision-type memory — verdict with cited memory IDs
"""

import logging
import agent_base
from config import AgentID

log = logging.getLogger(__name__)

AGENT_ID = AgentID.CODE_REVIEW

SYSTEM = """You are a senior code reviewer issuing a merge verdict for a multi-agent pipeline.
Your job is to check for internal consistency, completeness, and risk across ALL fleet decisions.

Your workflow — follow this order exactly:
Step 1 — Call memclaw_recall (up to 3 times) to retrieve the full fleet memory across all agents.
Step 2 — Call memclaw_insights to get an automated staleness/contradiction analysis on your own memories.
          NOTE: at default trust level, insights scope is "agent" — it analyzes only your own memories.
          Cross-agent contradiction detection is YOUR job in Step 3 based on what you recalled.
Step 3 — Review the recalled memories directly. Check specifically:
          - Do SEO choices contradict any performance rules? (e.g. external JS vs bundle constraint)
          - Are all critical decisions documented? (HTML structure, CSS, CWV rules, SEO tags)
          - Any unresolved blockers from insights?
Step 4 — Call memclaw_write once to save your final verdict as a decision-type memory.
          Your verdict memory MUST cite specific memory IDs from what you recalled.

Verdict memory format (Step 4 write):
  content: "Code Review Verdict: <LGTM|BLOCK>. <2-3 sentence reasoning>. Cited memory IDs: <ids>. Contradictions: <list or none>."
  importance: 0.95
  tags: ["codereview", "verdict", "<lgtm or block>", "final"]
"""

PROMPT = """You are the Code Review Agent — the final gatekeeper before this pipeline's output ships.
Frontend, Performance, and SEO agents have all written their decisions to fleet memory.

Your task — follow these steps in order:
1. Call memclaw_recall with query "HTML5 semantic structure CSS Grid layout critical CSS frontend decisions" to get frontend memories
2. Call memclaw_recall with query "Core Web Vitals bundle size performance rules lazy loading images" to get performance memories
3. Call memclaw_recall with query "SEO title meta schema JSON-LD OG tags external JavaScript" to get SEO memories
4. Call memclaw_insights to detect contradictions between agents
5. Review the full picture:
   - Are the SEO schema decisions consistent with the Performance bundle rules (no external JS)?
   - Do the frontend layout decisions support the CWV targets the Performance agent set?
   - Is anything missing or contradictory?
6. Call memclaw_write once with your final verdict (importance=0.95, tags=["codereview","verdict","final"])

Issue LGTM if everything is consistent and production-ready.
Issue BLOCK only if there is a REAL contradiction (e.g. SEO requires loading an external JS schema library but Performance explicitly forbids external JS).
Inline JSON-LD is NOT a contradiction with "no external JS" — it is the correct compliant choice.
Always cite the memory IDs you relied on for your verdict."""

ALLOWED_TOOLS = ["memclaw_recall", "memclaw_insights", "memclaw_write"]


def run() -> dict:
    log.info("[Code Review Agent] Starting — recall + insights + verdict")
    result = agent_base.run_agent(
        agent_id=AGENT_ID,
        system=SYSTEM,
        user_prompt=PROMPT,
        allowed_tools=ALLOWED_TOOLS,
    )
    recalls  = [c for c in result["tool_calls"] if c["tool"] == "memclaw_recall"]
    insights = [c for c in result["tool_calls"] if c["tool"] == "memclaw_insights"]
    writes   = [c for c in result["tool_calls"] if c["tool"] == "memclaw_write"]
    log.info("[Code Review Agent] recall×%d  insights×%d  write×%d",
             len(recalls), len(insights), len(writes))

    verdict_line = next(
        (line for line in result["final_text"].splitlines() if "LGTM" in line or "BLOCK" in line),
        ""
    )
    if verdict_line:
        log.info("[Code Review Agent] Verdict: %s", verdict_line.strip())
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    out = run()
    print("\n--- Final response ---")
    print(out["final_text"])

