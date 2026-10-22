"""
Central configuration — agent IDs, memory types, and constants.
Import from here instead of hardcoding strings across agent files.
"""

from enum import Enum


class MemoryType(str, Enum):
    decision = "decision"
    fact      = "fact"
    rule      = "rule"
    insight   = "insight"


class AgentID:
    FRONTEND     = "frontend-agent"
    PERFORMANCE  = "performance-agent"
    SEO          = "seo-agent"
    CODE_REVIEW  = "code-review-agent"
    MANAGER      = "manager-tenant"
    # Owns fleet teardown (--reset / --loop). Kept separate from MANAGER so the
    # audit trail never shows the read-only oversight agent deleting memories.
    ORCHESTRATOR = "orchestrator"


MAX_MEMORY_CONTENT_LEN = 4000   # chars; MemClaw API limit (undocumented, conservative)
MEMCLAW_API_DOMAIN     = "memclaw.net"
