"""Extension physical call-site identity and retained source identity contracts."""

import pytest

from NodeForge.compiler_identities import (
    CallSiteId, ExtensionCallSiteId, GroupCompilationIdentity, local_function_id,
)
from NodeForge.extensions.contracts import ExtensionCallableId
from NodeForge.function_instances import (
    extension_instance_key_for, function_group_owner_scope, instance_key_for,
)
from NodeForge.semantic.body import _BodyIdentityAllocator, _BodySemanticState


def _allocator(physical="root-A"):
    return _BodyIdentityAllocator(
        owner_scope="stable-source", declaration_owner="stable-source",
        next_local_id=0, ordinary_reservations={}, structural_reservations={},
        input_declaration_ordinals={}, call_occurrence_ordinals={},
        physical_owner_scope=physical,
    )


def test_extension_occurrences_are_owner_scoped_per_public_callee_and_nonrewinding():
    """Syntax occurrences of the same public extension are separate and fork-safe."""
    ids = _allocator()
    sin = ExtensionCallableId(("packages", "math"), "sin")
    cos = ExtensionCallableId(("packages", "math"), "cos")
    first = ids.allocate_extension_call_site_id(sin)
    sibling = ids.allocate_extension_call_site_id(cos)
    nested = ids.allocate_extension_call_site_id(sin)
    assert (first.ordinal, sibling.ordinal, nested.ordinal) == (0, 0, 1)
    assert first.owner_scope == nested.owner_scope == "root-A"
    assert extension_instance_key_for(first) != extension_instance_key_for(nested)
    assert extension_instance_key_for(first) != extension_instance_key_for(
        _allocator("root-B").allocate_extension_call_site_id(sin)
    )
    assert extension_instance_key_for(first) == extension_instance_key_for(
        _allocator().allocate_extension_call_site_id(sin)
    )

    # Source identities continue using their old separate allocator and hash bytes.
    function_id = local_function_id("stable-source", "helper", "x:FLOAT")
    source_call = ids.allocate_call_site_id(function_id)
    assert source_call == CallSiteId("stable-source", function_id, 0)
    assert instance_key_for(source_call) == instance_key_for(CallSiteId("stable-source", function_id, 0))


def test_extension_ordinal_allocator_is_shared_by_forked_body_states():
    """Forking a branch does not rewind the physical occurrence counter."""
    ids = _allocator()
    # The semantic body forks its mapping state, not its identity allocator.
    a = ids.allocate_extension_call_site_id(ExtensionCallableId(("vendor",), "run"))
    b = ids.allocate_extension_call_site_id(ExtensionCallableId(("vendor",), "run"))
    assert [a.ordinal, b.ordinal] == [0, 1]
    assert ids.extension_occurrence_ordinals[ExtensionCallableId(("vendor",), "run")] == 2


def test_physical_identity_rejects_invalid_owner_callee_and_ordinal():
    """Do not persist physically ambiguous extension identities."""
    callee = ExtensionCallableId(("vendor",), "sample")
    for owner in ("", None, 7):
        with pytest.raises((TypeError, ValueError)):
            ExtensionCallSiteId(owner, callee, 0)
    for ordinal in (-1, True, "1"):
        with pytest.raises((TypeError, ValueError)):
            ExtensionCallSiteId("owner", callee, ordinal)
    with pytest.raises(TypeError):
        ExtensionCallSiteId("owner", local_function_id("scope", "run", ""), 0)
    with pytest.raises(TypeError):
        extension_instance_key_for(CallSiteId("owner", local_function_id("scope", "run", ""), 0))
    with pytest.raises(ValueError):
        GroupCompilationIdentity(None, "owner", "def", "decl", physical_owner_scope="")


def test_physical_source_scope_separates_roots_from_stable_source_origin():
    """Two physical roots may share a source owner without sharing extension groups."""
    stable = function_group_owner_scope("COMPOSITION", "text:shared")
    a = GroupCompilationIdentity(None, stable, stable, stable,
                                 physical_owner_scope=function_group_owner_scope("ROOT", "a"))
    b = GroupCompilationIdentity(None, stable, stable, stable,
                                 physical_owner_scope=function_group_owner_scope("ROOT", "b"))
    assert a.owner_scope == b.owner_scope
    assert a.physical_owner_scope != b.physical_owner_scope
