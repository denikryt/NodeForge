"""Pure tests for frontend-owned runtime binding identity and snapshots."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from NodeForge.compiler_identities import BindingId
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import FrontendRuntimeBindings, RuntimeBindingSymbol


def test_bind_reuses_name_identity_and_updates_type():
    """Rebinding one source slot keeps its BindingId while replacing semantic type metadata."""
    bindings = FrontendRuntimeBindings("owner")
    first = bindings.bind("x", NFType.FLOAT)
    rebound = bindings.bind("x", NFType.VECTOR)

    assert first.binding_id == BindingId("owner", 0)
    assert rebound.binding_id == first.binding_id
    assert rebound.typ is NFType.VECTOR


def test_eager_allocation_survives_unbind_and_never_reuses_local_id():
    """First ordinary bind reserves an ID before snapshots and unbind never rewinds allocation."""
    bindings = FrontendRuntimeBindings("owner")
    x0 = bindings.bind("x", NFType.FLOAT)
    bindings.unbind("x")
    y = bindings.bind("y", NFType.FLOAT)
    x1 = bindings.bind("x", NFType.INT)

    assert x0.binding_id.local_id == 0
    assert y.binding_id.local_id == 1
    assert x1.binding_id == x0.binding_id


def test_snapshot_is_detached_and_read_only():
    """Frontend consumers cannot mutate the active compiler table through snapshots."""
    bindings = FrontendRuntimeBindings("owner")
    first = bindings.bind("x", NFType.FLOAT)
    snapshot = bindings.snapshot()

    assert isinstance(snapshot, MappingProxyType)
    assert snapshot["x"] == first
    with pytest.raises(TypeError):
        snapshot["y"] = first

    bindings.bind("x", NFType.VECTOR)
    assert snapshot["x"].typ is NFType.FLOAT


def test_restore_active_state_keeps_historical_reservations_and_counter():
    """Checkpoint restore changes active names without making reserved IDs reusable."""
    bindings = FrontendRuntimeBindings("owner")
    x = bindings.bind("x", NFType.FLOAT)
    checkpoint = bindings.snapshot()
    bindings.bind("y", NFType.FLOAT)
    bindings.restore_active_state(checkpoint)
    z = bindings.bind("z", NFType.FLOAT)

    assert bindings.get("x") == x
    assert bindings.get("y") is None
    assert z.binding_id.local_id == 2


def test_restore_rejects_unknown_or_wrong_owner_binding_ids_without_mutation():
    """Candidate validation rejects impossible active states before replacing current bindings."""
    bindings = FrontendRuntimeBindings("owner")
    x = bindings.bind("x", NFType.FLOAT)
    before = bindings.snapshot()

    with pytest.raises(ValueError):
        bindings.restore_active_state({"x": RuntimeBindingSymbol(BindingId("other", 0), NFType.FLOAT)})
    assert bindings.snapshot() == before

    with pytest.raises(ValueError):
        bindings.restore_active_state({"missing": RuntimeBindingSymbol(BindingId("owner", 99), NFType.FLOAT)})
    assert bindings.snapshot() == before


def _load_compiler(monkeypatch):
    """Import compiler with a minimal bpy stub and return a bare initialized binding owner."""
    import importlib
    import sys
    from types import ModuleType

    monkeypatch.setitem(sys.modules, "bpy", ModuleType("bpy"))
    module = importlib.import_module("NodeForge.compiler")
    compiler = object.__new__(module.Compiler)
    compiler.function_group_owner_scope = "runtime-binding-test"
    compiler._runtime_bindings = FrontendRuntimeBindings(compiler.function_group_owner_scope)
    compiler._runtime_binding_values = {}
    compiler._legacy_structural_bindings = {}
    from NodeForge.compile_time import CompileTimeState
    compiler.compile_time = CompileTimeState()
    return module, compiler


def test_compiler_binding_ownership_is_exclusive_and_structural_validation_is_precommit(monkeypatch):
    """Compiler mutation APIs enforce runtime/structural exclusivity without partial invalid writes."""
    from NodeForge.values import Value

    _module, compiler = _load_compiler(monkeypatch)
    first = Value(object(), NFType.FLOAT)
    symbol = compiler.bind_runtime_value("x", first)

    with pytest.raises(TypeError):
        compiler.bind_legacy_structural("x", first)
    assert compiler.runtime_binding("x") == symbol
    assert compiler.runtime_value("x") is first
    assert not compiler.has_legacy_structural_binding("x")

    array = [first]
    compiler.bind_legacy_structural("x", array)
    assert compiler.runtime_binding("x") is None
    assert compiler.legacy_structural_binding("x") is array

    second = Value(object(), NFType.VECTOR)
    rebound = compiler.bind_runtime_value("x", second)
    assert rebound.binding_id == symbol.binding_id
    assert compiler.runtime_value("x") is second
    assert not compiler.has_legacy_structural_binding("x")


def test_compiler_checkpoint_restores_object_identity_without_rewinding_allocator(monkeypatch):
    """Binding-map checkpoints restore exact Values while preserving IDs allocated speculatively."""
    from NodeForge.values import Value

    module, compiler = _load_compiler(monkeypatch)
    first = Value(object(), NFType.FLOAT)
    first_symbol = compiler.bind_runtime_value("x", first)
    structural = [first]
    compiler.bind_legacy_structural("items", structural)
    checkpoint = compiler._snapshot_binding_state()

    speculative = Value(object(), NFType.VECTOR)
    compiler.bind_runtime_value("x", speculative)
    y = compiler.bind_runtime_value("y", first)
    compiler._restore_binding_state(checkpoint)

    assert compiler.runtime_binding("x").binding_id == first_symbol.binding_id
    assert compiler.runtime_value("x") is first
    assert compiler.legacy_structural_binding("items") is structural
    assert compiler.runtime_binding("y") is None
    z = compiler.bind_runtime_value("z", first)
    assert z.binding_id.local_id == y.binding_id.local_id + 1

    invalid = module._CompilerBindingState(
        checkpoint.runtime_symbols,
        {first_symbol.binding_id: Value(object(), NFType.VECTOR)},
        checkpoint.legacy_structural,
    )
    with pytest.raises(Exception):
        compiler._restore_binding_state(invalid)
    assert compiler.runtime_value("x") is first



def test_structural_store_accepts_package_compile_time_subclass_without_losing_runtime_state(monkeypatch):
    """Package-owned CompileTimeObject subclasses use the generic structural extension boundary."""
    from NodeForge.compile_time import CompileTimeObject
    from NodeForge.values import Value

    class PackageState(CompileTimeObject):
        """Synthetic package-system state used to exercise the public extension protocol."""

    _module, compiler = _load_compiler(monkeypatch)
    previous = Value(object(), NFType.FLOAT)
    compiler.bind_runtime_value("state", previous)
    package_state = PackageState()

    compiler.bind_legacy_structural("state", package_state)

    assert compiler.runtime_binding("state") is None
    assert compiler.runtime_value("state") is None
    assert compiler.legacy_structural_binding("state") is package_state


def test_assignment_accepts_compile_time_object_from_resolved_system_constructor(monkeypatch):
    """Resolved package systems may return assignable CompileTimeObject subclasses unchanged."""
    import ast
    import types

    from NodeForge.compile_time import CompileTimeObject
    from NodeForge.statement_compiler import GroupBuildContext, compile_statement
    from NodeForge.systems import registry as systems_registry

    class PackageState(CompileTimeObject):
        """Synthetic compile-time object returned by a resolved package system."""

    module, compiler = _load_compiler(monkeypatch)
    binding = types.SimpleNamespace(name="package_state")
    compiler.resolved_environment = types.SimpleNamespace(system_constructors={"package_state": binding})
    compiler.imported_library_functions = {}
    compiler.local_functions = {}
    compiler.backend_builtins = {}
    compiler.consts = {}
    compiler.reserved_name_labels = {}
    compiler.group = object()
    compiler.depth = 0
    state = PackageState()

    def compile_resolved_call(received_comp, expr, received_binding, depth=0):
        assert received_comp is compiler
        assert received_binding is binding
        assert expr.func.id == "package_state"
        return state

    monkeypatch.setattr(systems_registry, "compile_resolved_call", compile_resolved_call)
    from NodeForge import expression_compiler
    monkeypatch.setattr(expression_compiler.systems_registry, "compile_resolved_call", compile_resolved_call)
    ctx = GroupBuildContext(group=compiler.group, comp=compiler, geometry_mode=False)
    stmt = ast.parse("state = package_state()").body[0]

    compile_statement(ctx, stmt)

    assert compiler.legacy_structural_binding("state") is state
    assert compiler.runtime_binding("state") is None

def test_runtime_binding_module_has_no_blender_dependency():
    """Frontend binding ownership remains independent of values.py and Blender modules."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "runtime_bindings.py").read_text(encoding="utf-8")
    assert "from .values" not in source
    assert "import bpy" not in source
    assert "from .nodes" not in source


def test_private_binding_stores_are_not_accessed_outside_compiler_production_code():
    """Compiler is the sole production mutation/read boundary for all private binding stores."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    forbidden = ("._runtime_bindings", "._runtime_binding_values", "._legacy_structural_bindings")
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "compiler.py" or "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                offenders.append((str(path.relative_to(root)), token))
    assert offenders == []


def test_runtime_binding_mutation_does_not_implicitly_change_compile_time_state(monkeypatch):
    """Runtime binding ownership remains independent from same-name compile-time state."""
    from NodeForge.values import Value

    _module, compiler = _load_compiler(monkeypatch)
    compiler.compile_time.bind("x", 7)
    compiler.bind_runtime_value("x", Value(object(), NFType.FLOAT))
    assert compiler.compile_time.get("x") == 7


def test_binding_checkpoint_excludes_compile_time_state(monkeypatch):
    """Legacy runtime/structural checkpoints never roll compile-time state backward."""
    _module, compiler = _load_compiler(monkeypatch)
    compiler.compile_time.bind("c", 1)
    checkpoint = compiler._snapshot_binding_state()
    compiler.compile_time.bind("c", 2)
    compiler._restore_binding_state(checkpoint)
    assert compiler.compile_time.get("c") == 2
