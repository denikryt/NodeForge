"""Unit contracts for canonical compiler-owned identities."""

from dataclasses import FrozenInstanceError

import pytest

from NodeForge.compiler_identities import (
    InterfaceInputOrigin,
    InputDeclarationId,
    BindingId,
    CallSiteId,
    CORE_PACKAGE_ID,
    library_function_id,
    local_function_id,
)
from NodeForge.function_instances import function_group_owner_scope, instance_key_for
from NodeForge.nf_types import NFType

pytestmark = pytest.mark.unit

_ROOT_OWNER = function_group_owner_scope("ROOT", "0123456789abcdef0123456789abcdef")
_LEGACY_LOCAL = '{"definition_owner":"{\\"instance\\":\\"SHARED\\",\\"kind\\":\\"ROOT\\",\\"parts\\":[\\"0123456789abcdef0123456789abcdef\\"]}","kind":"LOCAL_DEF","name":"helper","signature":"x:FLOAT"}'
_LEGACY_LIBRARY = '{"kind":"LIBRARY","name":"helper","namespace":"functions","package_id":"__nodeforge_core__"}'


def test_identity_records_are_immutable_hashable_and_structural():
    binding = BindingId(_ROOT_OWNER, 0)
    function = local_function_id(_ROOT_OWNER, "helper", "x:FLOAT")
    call_site = CallSiteId(_ROOT_OWNER, function, 0)
    assert len({binding, BindingId(_ROOT_OWNER, 0)}) == 1
    assert len({function, local_function_id(_ROOT_OWNER, "helper", "x:FLOAT")}) == 1
    assert len({call_site, CallSiteId(_ROOT_OWNER, function, 0)}) == 1
    with pytest.raises(FrozenInstanceError):
        binding.local_id = 1


def test_negative_binding_and_callsite_ids_are_rejected():
    function = local_function_id(_ROOT_OWNER, "helper", "x:FLOAT")
    with pytest.raises(ValueError):
        BindingId(_ROOT_OWNER, -1)
    with pytest.raises(ValueError):
        CallSiteId(_ROOT_OWNER, function, -1)


def test_local_function_id_stable_key_exactly_preserves_legacy_json():
    function = local_function_id(_ROOT_OWNER, "helper", "x:FLOAT")
    assert function.stable_key() == _LEGACY_LOCAL
    assert function != local_function_id(function_group_owner_scope("ROOT", "other"), "helper", "x:FLOAT")
    assert function != local_function_id(_ROOT_OWNER, "other", "x:FLOAT")
    assert function != local_function_id(_ROOT_OWNER, "helper", "x:VECTOR")


def test_library_function_id_stable_key_and_core_normalization_are_exact():
    from_none = library_function_id("functions", None, "helper")
    from_empty = library_function_id("functions", "", "helper")
    assert from_none == from_empty
    assert from_none.package_id == CORE_PACKAGE_ID
    assert from_none.stable_key() == _LEGACY_LIBRARY
    assert from_none != library_function_id("examples", None, "helper")
    assert from_none != library_function_id("functions", "pkg.other", "helper")
    assert from_none != library_function_id("functions", None, "other")


def test_callsite_instance_key_exactly_preserves_legacy_digest_vectors():
    local = local_function_id(_ROOT_OWNER, "helper", "x:FLOAT")
    library = library_function_id("functions", None, "helper")
    assert instance_key_for(CallSiteId(_ROOT_OWNER, local, 0)) == "41b0b35ed1633716105f59a9881b3d52"
    assert instance_key_for(CallSiteId(_ROOT_OWNER, local, 1)) == "68acf8f2925b1e190cb9d67668e80e89"
    assert instance_key_for(CallSiteId(_ROOT_OWNER, library, 0)) == "fdedf9d34c87c1544e3ad8e8cb315e2b"
    assert instance_key_for(CallSiteId(_ROOT_OWNER, library, 1)) == "d0c2cda4fe4c81017782eecdbdeb410e"


def test_callsite_identity_and_digest_are_owner_callee_and_ordinal_sensitive():
    local = local_function_id(_ROOT_OWNER, "helper", "x:FLOAT")
    other_local = local_function_id(_ROOT_OWNER, "other", "x:FLOAT")
    other_owner = function_group_owner_scope("ROOT", "other")
    base = CallSiteId(_ROOT_OWNER, local, 0)
    assert base != CallSiteId(_ROOT_OWNER, local, 1)
    assert base != CallSiteId(_ROOT_OWNER, other_local, 0)
    assert base != CallSiteId(other_owner, local, 0)
    assert instance_key_for(base) != instance_key_for(CallSiteId(_ROOT_OWNER, local, 1))
    assert instance_key_for(base) != instance_key_for(CallSiteId(_ROOT_OWNER, other_local, 0))
    assert instance_key_for(base) != instance_key_for(CallSiteId(other_owner, local, 0))


def _load_compiler_without_blender(monkeypatch):
    """Import compiler.py with a temporary bpy stub for pure method tests."""
    import importlib
    import sys
    from types import ModuleType

    before = set(sys.modules)
    monkeypatch.setitem(sys.modules, "bpy", ModuleType("bpy"))
    module = importlib.import_module("NodeForge.compiler")
    return module, before


def _cleanup_compiler_imports(before):
    """Remove temporary compiler modules loaded through the bpy stub."""
    import sys

    for name in set(sys.modules) - before:
        if name == "bpy" or name.startswith("NodeForge."):
            sys.modules.pop(name, None)


def test_compiler_runtime_binding_slots_are_frontend_owned_and_backend_coherent(monkeypatch):
    from NodeForge.values import Value

    module, before = _load_compiler_without_blender(monkeypatch)
    try:
        compiler = object.__new__(module.Compiler)
        compiler.function_group_owner_scope = _ROOT_OWNER
        compiler._runtime_bindings = module.FrontendRuntimeBindings(_ROOT_OWNER)
        compiler._runtime_binding_values = {}
        compiler._legacy_structural_bindings = {}
        first = Value(object(), NFType.FLOAT)
        second = Value(object(), NFType.VECTOR)

        first_symbol = compiler.bind_runtime_value("a", first)
        first_id = first_symbol.binding_id
        snapshot = compiler.runtime_bindings_snapshot()
        backend = compiler.backend_runtime_values_snapshot()
        assert snapshot["a"].binding_id == first_id
        assert snapshot["a"].typ is NFType.FLOAT
        assert backend[first_id] is first
        with pytest.raises(TypeError):
            snapshot["b"] = snapshot["a"]
        with pytest.raises(TypeError):
            backend[first_id] = second

        rebound = compiler.bind_runtime_value("a", second)
        assert rebound.binding_id == first_id
        assert rebound.typ is NFType.VECTOR
        assert compiler.runtime_value("a") is second

        compiler.bind_legacy_structural("a", [])
        assert compiler.runtime_binding("a") is None
        assert compiler.legacy_structural_binding("a") == []

        restored = compiler.bind_runtime_value("a", first)
        assert restored.binding_id == first_id
        assert compiler.runtime_value("a") is first
        assert not compiler.has_legacy_structural_binding("a")

        other = compiler.bind_runtime_value("b", first)
        assert other.binding_id != first_id
        assert other.binding_id.owner_scope == _ROOT_OWNER
        assert BindingId(function_group_owner_scope("ROOT", "other"), first_id.local_id) != first_id
    finally:
        _cleanup_compiler_imports(before)


def test_body_identity_allocator_owns_monotonic_source_call_occurrence_sequence():
    """Unique source-call ordinals belong to the non-rewinding semantic body allocator."""
    from NodeForge.semantic_body import _BodyIdentityAllocator

    first = local_function_id(_ROOT_OWNER, "first", "x:FLOAT")
    second = local_function_id(_ROOT_OWNER, "second", "x:FLOAT")
    allocator = _BodyIdentityAllocator(
        owner_scope=_ROOT_OWNER,
        declaration_owner="declarations",
        next_local_id=0,
        ordinary_reservations={},
        structural_reservations={},
        input_declaration_ordinals={},
        call_occurrence_ordinals={},
    )

    assert allocator.allocate_call_site_id(first) == CallSiteId(_ROOT_OWNER, first, 0)
    assert allocator.allocate_call_site_id(first) == CallSiteId(_ROOT_OWNER, first, 1)
    assert allocator.allocate_call_site_id(second) == CallSiteId(_ROOT_OWNER, second, 0)
    assert allocator.call_occurrence_ordinals == {first: 2, second: 1}


def test_interface_input_origin_is_exact_binding_or_input_declaration_union():
    """Panel provenance reuses existing physical declaration identities without another allocator."""
    assert InterfaceInputOrigin == (BindingId | InputDeclarationId)
