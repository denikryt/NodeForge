"""Pure contracts for the shared NodeForge attribute-domain authority."""

import pytest

from NodeForge.semantic.attribute_domains import ATTRIBUTE_DOMAINS, normalize_attribute_domain
from NodeForge.errors import CompileError


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("domain", sorted(ATTRIBUTE_DOMAINS))
def test_attribute_domain_accepts_each_canonical_token_and_is_idempotent(domain):
    """Every public domain token remains stable under repeated normalization."""
    normalized = normalize_attribute_domain(domain, "test()")
    assert normalized == domain
    assert normalize_attribute_domain(normalized, "test()") == domain


@pytest.mark.parametrize(
    ("source", "expected"),
    [("point", "POINT"), ("FaCe", "FACE"), ("curve", "CURVE")],
)
def test_attribute_domain_canonicalizes_case(source, expected):
    """Case normalization is shared semantic behavior, not backend behavior."""
    assert normalize_attribute_domain(source, "test()") == expected


@pytest.mark.parametrize("domain", ["", "LAYER", "not_a_domain", None, 1])
def test_attribute_domain_rejects_empty_unknown_and_non_string_values_without_defaulting(domain):
    """Falsey or invalid inputs never become the consumer-owned POINT default."""
    with pytest.raises(
        CompileError,
        match=r"Unsupported test\(\) domain\. Use POINT, EDGE, FACE, CORNER, CURVE or INSTANCE",
    ):
        normalize_attribute_domain(domain, "test()")
