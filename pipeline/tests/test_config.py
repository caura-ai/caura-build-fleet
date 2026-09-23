"""Unit tests for config.py — agent IDs, memory types, and constants."""

from config import AgentID, MemoryType, MAX_MEMORY_CONTENT_LEN, MEMCLAW_API_DOMAIN


# ── MemoryType enum ───────────────────────────────────────────────────────────

def test_memory_type_has_exactly_four_members():
    assert len(MemoryType) == 4


def test_memory_type_values_are_lowercase_strings():
    for member in MemoryType:
        assert member.value == member.value.lower()
        assert isinstance(member.value, str)


def test_memory_type_members():
    assert MemoryType.decision == "decision"
    assert MemoryType.fact == "fact"
    assert MemoryType.rule == "rule"
    assert MemoryType.insight == "insight"


def test_memory_type_is_str_subclass():
    """MemoryType inherits from str so members can be used directly as strings."""
    for member in MemoryType:
        assert isinstance(member, str)


def test_memory_type_lookup_by_value():
    assert MemoryType("decision") is MemoryType.decision
    assert MemoryType("fact") is MemoryType.fact
    assert MemoryType("rule") is MemoryType.rule
    assert MemoryType("insight") is MemoryType.insight


# ── AgentID constants ─────────────────────────────────────────────────────────

def test_agent_id_has_five_agents():
    ids = [AgentID.FRONTEND, AgentID.PERFORMANCE, AgentID.SEO,
           AgentID.CODE_REVIEW, AgentID.MANAGER]
    assert len(ids) == 5


def test_agent_ids_are_non_empty_strings():
    for attr in ("FRONTEND", "PERFORMANCE", "SEO", "CODE_REVIEW", "MANAGER"):
        value = getattr(AgentID, attr)
        assert isinstance(value, str)
        assert len(value) > 0


def test_agent_ids_are_unique():
    ids = [AgentID.FRONTEND, AgentID.PERFORMANCE, AgentID.SEO,
           AgentID.CODE_REVIEW, AgentID.MANAGER]
    assert len(ids) == len(set(ids))


def test_agent_id_values():
    assert AgentID.FRONTEND == "frontend-agent"
    assert AgentID.PERFORMANCE == "performance-agent"
    assert AgentID.SEO == "seo-agent"
    assert AgentID.CODE_REVIEW == "code-review-agent"
    assert AgentID.MANAGER == "manager-tenant"


def test_agent_ids_use_lowercase_kebab_case():
    """All agent IDs should follow kebab-case (lowercase + hyphens) convention."""
    for attr in ("FRONTEND", "PERFORMANCE", "SEO", "CODE_REVIEW", "MANAGER"):
        value = getattr(AgentID, attr)
        assert value == value.lower(), f"{attr} is not lowercase"
        assert " " not in value, f"{attr} contains spaces"
        assert "_" not in value, f"{attr} contains underscores"


# ── Global constants ──────────────────────────────────────────────────────────

def test_max_memory_content_len_is_positive_int():
    assert isinstance(MAX_MEMORY_CONTENT_LEN, int)
    assert MAX_MEMORY_CONTENT_LEN > 0


def test_max_memory_content_len_value():
    assert MAX_MEMORY_CONTENT_LEN == 4000


def test_memclaw_api_domain_is_non_empty_string():
    assert isinstance(MEMCLAW_API_DOMAIN, str)
    assert len(MEMCLAW_API_DOMAIN) > 0


def test_memclaw_api_domain_value():
    assert MEMCLAW_API_DOMAIN == "memclaw.net"


def test_memclaw_api_domain_has_no_protocol_prefix():
    """Domain should be bare (no https://) since callers prepend the protocol."""
    assert not MEMCLAW_API_DOMAIN.startswith("http")
