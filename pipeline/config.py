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


MAX_MEMORY_CONTENT_LEN = 4000   # chars; MemClaw API limit (undocumented, conservative)
MEMCLAW_API_DOMAIN     = "memclaw.net"


# Tools that can change stored memory. The Manager is the one agent the README
# advertises as read-only, so its audit has to recognise every one of these; both
# the Manager's own report and the orchestrator's isolation summary consume this
# set, and they must agree.
#
# Listed explicitly rather than matched by substring: a tool named for what it does
# to memory ("memclaw_forget") would slip past a `"write" in name or "manage" in
# name` test and the audit would report isolation that was never enforced.
#
# memclaw_manage is included even though op="read" is a plain lookup — it can carry
# op="update" or op="delete", which a call log cannot distinguish in advance.
MUTATING_TOOLS = frozenset({
    "memclaw_write",
    "memclaw_manage",
})


def is_mutating_tool(tool_name: object) -> bool:
    """True if `tool_name` names a tool that can change stored memory.

    Accepts any type so callers can pass a log entry's field directly without
    guarding for a missing or null name first.
    """
    return isinstance(tool_name, str) and tool_name in MUTATING_TOOLS


def mutating_calls(tool_calls: list[dict]) -> list[dict]:
    """Return the entries of `tool_calls` that invoked a mutating tool."""
    return [c for c in tool_calls if is_mutating_tool(c.get("tool"))]
