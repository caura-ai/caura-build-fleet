"""Unit tests for pipeline/config.py string contracts (#10)."""

from config import AgentID, MAX_MEMORY_CONTENT_LEN, MEMCLAW_API_DOMAIN, MemoryType


def test_agent_id_values_match_trust_registration_contract():
    # These exact strings must match MemClaw admin trust API registrations.
    assert AgentID.FRONTEND == "frontend-agent"
    assert AgentID.PERFORMANCE == "performance-agent"
    assert AgentID.SEO == "seo-agent"
    assert AgentID.CODE_REVIEW == "code-review-agent"
    assert AgentID.MANAGER == "manager-tenant"


def test_memory_type_values():
    assert MemoryType.decision.value == "decision"
    assert MemoryType.fact.value == "fact"
    assert MemoryType.rule.value == "rule"
    assert MemoryType.insight.value == "insight"
    assert set(MemoryType) == {
        MemoryType.decision,
        MemoryType.fact,
        MemoryType.rule,
        MemoryType.insight,
    }


def test_constants():
    assert MAX_MEMORY_CONTENT_LEN == 4000
    assert MEMCLAW_API_DOMAIN == "memclaw.net"
