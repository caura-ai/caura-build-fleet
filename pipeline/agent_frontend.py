"""
Fleet 1 — Frontend Agent
Role  : Architect the SaaS landing page. First in chain — nothing to recall.
Tools : memclaw_write only (no recall needed)
Writes: HTML5 semantics decision, CSS Grid layout decision, critical CSS strategy (importance 0.95)
"""

import logging
import agent_base
from config import AgentID

log = logging.getLogger(__name__)

AGENT_ID = AgentID.FRONTEND

SYSTEM = """You are a senior frontend engineer specialising in SaaS landing pages.
You make concrete architectural decisions and immediately persist them as MemClaw memories
so the next agent in the pipeline can recall them.

When you call memclaw_write, always include:
- type: "decision" or "fact"
- content: a clear, self-contained statement of the decision
- importance: a float 0.0–1.0 reflecting how critical this is for the page
- tags: a list of relevant labels

Write at minimum 3 memories:
1. HTML5 semantic structure (which elements and sections)
2. CSS layout strategy (Grid columns, breakpoints)
3. Critical CSS approach (what is inlined, importance score: 0.95)
"""

PROMPT = """Architect a production-ready SaaS landing page for MemClaw — a multi-agent shared memory platform.

Decide and document:
1. HTML5 semantic structure — which elements (<header>, <main>, <section>, <article>, <footer>, etc.)
   and what sections the page has (hero, stats, pipeline, features, memory table, CTA)
2. CSS layout strategy — CSS Grid column count, breakpoints (mobile/tablet/desktop),
   whether Flexbox is used inside Grid cells
3. Critical CSS strategy — what goes inline in <style>, what is deferred,
   and set importance_score = 0.95 for the critical CSS fact memory

Use memclaw_write to save each decision to the fleet. Then summarise what you decided."""

# Only allow write — this agent has nothing to recall yet
ALLOWED_TOOLS = ["memclaw_write"]


def run() -> dict:
    log.info("[Frontend Agent] Starting — first in chain, no recall")
    result = agent_base.run_agent(
        agent_id=AGENT_ID,
        system=SYSTEM,
        user_prompt=PROMPT,
        allowed_tools=ALLOWED_TOOLS,
    )
    writes = [c for c in result["tool_calls"] if c["tool"] == "memclaw_write"]
    log.info("[Frontend Agent] memclaw_write called %d time(s)", len(writes))
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    out = run()
    print("\n--- Final response ---")
    print(out["final_text"])

