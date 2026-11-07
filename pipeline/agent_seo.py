"""
Fleet 3 — SEO Agent
Role  : Pick schema library respecting bundle constraint; write SEO decisions.
Tools : memclaw_recall (reads ALL fleet memories) then memclaw_write
Writes: title/meta strategy, schema markup choice, OG tags — explicitly informed by Performance's rules
"""

import logging
import agent_base
from config import AgentID

log = logging.getLogger(__name__)

AGENT_ID = AgentID.SEO

SYSTEM = """You are an SEO specialist. Your decisions are always constrained by what other
fleet agents have already decided. Your workflow is: RECALL EVERYTHING, then DECIDE, then WRITE.

Step 1 — Call memclaw_recall with a broad query to retrieve ALL fleet decisions and rules.
Step 2 — Read carefully. If the Performance Agent wrote a rule about zero external JS or
          a bundle size constraint, your schema markup choice MUST respect it
          (use inline JSON-LD, never recommend an external schema library).
Step 3 — Call memclaw_write to save your SEO decisions as decision-type memories.

When writing memories omit the memory_type field and let MemClaw auto-classify. Set importance 0.85–0.95.
Write at minimum 2 memories:
1. Title + meta description strategy (keyword, brand, character limits)
2. Schema markup approach + OpenGraph tags (cite the performance constraint you respected)
"""

PROMPT = """You are the SEO Agent. Frontend and Performance agents have already written their
decisions and rules to fleet memory.

Your task:
1. Call memclaw_recall to retrieve ALL fleet memories (query: "architecture CSS layout performance bundle constraint images schema SEO")
2. Identify any performance constraints that affect your schema library choice
3. Make SEO decisions for the MemClaw SaaS landing page:
   - Title tag strategy (primary keyword + brand, < 60 chars)
   - Meta description (benefit-led, < 160 chars)
   - Schema.org markup approach — you MUST use inline JSON-LD if there is a zero-external-JS rule
   - OpenGraph tags for social sharing (og:title, og:description, og:type, og:image)
   - Canonical URL strategy
4. Write your decisions to fleet memory using memclaw_write

In your write calls, explicitly reference which performance rule shaped your schema choice.
After writing, summarise all SEO decisions made."""

ALLOWED_TOOLS = ["memclaw_recall", "memclaw_write"]


def run() -> dict:
    log.info("[SEO Agent] Starting — will recall ALL fleet memories first")
    result = agent_base.run_agent(
        agent_id=AGENT_ID,
        system=SYSTEM,
        user_prompt=PROMPT,
        allowed_tools=ALLOWED_TOOLS,
    )
    recalls = [c for c in result["tool_calls"] if c["tool"] == "memclaw_recall"]
    writes  = [c for c in result["tool_calls"] if c["tool"] == "memclaw_write"]
    log.info("[SEO Agent] recall×%d  write×%d", len(recalls), len(writes))
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    out = run()
    print("\n--- Final response ---")
    print(out["final_text"])

