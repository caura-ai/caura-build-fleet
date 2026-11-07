"""
Fleet 2 — Performance Agent
Role  : Audit frontend decisions against Core Web Vitals targets.
Tools : memclaw_recall (first) then memclaw_write
Writes: Bundle size constraint, lazy loading rules, image width/height rule (rule-type memories)
"""

import logging
import agent_base
from config import AgentID

log = logging.getLogger(__name__)

AGENT_ID = AgentID.PERFORMANCE

SYSTEM = """You are a web performance engineer specialising in Core Web Vitals (LCP, CLS, INP).
Your workflow is strictly: RECALL first, then ACT, then WRITE.

Step 1 — Call memclaw_recall with a query about frontend architecture decisions.
Step 2 — Read the recalled memories carefully. Note any CSS, layout, or image decisions.
Step 3 — Audit those decisions against CWV targets: LCP < 2.5s, CLS < 0.1, INP < 200ms.
Step 4 — Call memclaw_write to save your findings as rule-type memories.

When writing memories omit the memory_type field and let MemClaw auto-classify. Set importance 0.85–0.95.
Write at minimum 3 memories:
1. Bundle size constraint (this page has no build step — all CSS/JS must be inline)
2. Image rule: "All images must have explicit width and height attributes" (prevents CLS)
3. Lazy loading rules for below-fold content
"""

PROMPT = """You are the Performance Agent. The Frontend Agent has already written its architectural
decisions to fleet memory.

Your task:
1. Call memclaw_recall to retrieve the frontend decisions (query: "frontend architecture HTML5 CSS layout critical CSS")
2. Audit those decisions for Core Web Vitals compliance
3. Write your performance rules to fleet memory using memclaw_write

Focus especially on:
- Bundle size: since there is no build step, all assets must be inline — set a strict KB limit
- CLS prevention: every image needs explicit width + height
- LCP: hero image or text must load within 2.5s

After writing your rules, summarise what you found and what rules you set."""

ALLOWED_TOOLS = ["memclaw_recall", "memclaw_write"]


def run() -> dict:
    log.info("[Performance Agent] Starting — will recall frontend decisions first")
    result = agent_base.run_agent(
        agent_id=AGENT_ID,
        system=SYSTEM,
        user_prompt=PROMPT,
        allowed_tools=ALLOWED_TOOLS,
    )
    recalls = [c for c in result["tool_calls"] if c["tool"] == "memclaw_recall"]
    writes  = [c for c in result["tool_calls"] if c["tool"] == "memclaw_write"]
    log.info("[Performance Agent] recall×%d  write×%d", len(recalls), len(writes))
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    out = run()
    print("\n--- Final response ---")
    print(out["final_text"])

