"""
Central configuration — agent IDs and constants.
Import from here instead of hardcoding strings across agent files.
"""


class AgentID:
    FRONTEND     = "frontend-agent"
    PERFORMANCE  = "performance-agent"
    SEO          = "seo-agent"
    CODE_REVIEW  = "code-review-agent"
    MANAGER      = "manager-tenant"


MAX_MEMORY_CONTENT_LEN = 4000   # chars; MemClaw API limit (undocumented, conservative)
MEMCLAW_API_DOMAIN     = "memclaw.net"
