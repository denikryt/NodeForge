"""Pure contracts for compiler-owned compile-time state separation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from NodeForge.semantic.compile_time import CompileTimeSnapshot, CompileTimeState
from NodeForge.errors import CompileError


def test_state_crud_snapshot_fork_and_replace_are_shallow_and_detached():
    """State copies mapping membership while preserving contained object identity."""
    shared = [1, 2]
    state = CompileTimeState({"a": shared})
    assert state.contains("a")
    assert state.get("a") is shared

    with pytest.raises(TypeError):
        state.values["x"] = 1

    snapshot = state.snapshot()
    child = state.fork()
    state.bind("b", 2)
    child.bind("c", 3)
    assert "b" not in snapshot.values
    assert not state.contains("c")
    assert snapshot.values["a"] is shared
    assert child.get("a") is shared

    state.discard("a")
    state.replace(child.snapshot())
    assert state.get("a") is shared
    assert state.get("c") == 3


def test_snapshot_detaches_source_mapping_but_keeps_mutable_value_identity():
    """CompileTimeSnapshot is top-level detached without recursively freezing values."""
    shared = [1]
    source = {"a": shared}
    snapshot = CompileTimeSnapshot(source)
    source["b"] = 2
    assert "b" not in snapshot.values
    assert snapshot.values["a"] is shared


def test_adopted_backing_mapping_keeps_identity_across_replace():
    """Marker-1 compatibility can retain the historical constructor mapping object."""
    backing = {"a": 1}
    state = CompileTimeState(backing, adopt_mapping=True)
    state.replace(CompileTimeSnapshot({"b": 2}))
    assert backing == {"b": 2}
    assert state.values["b"] == 2


def test_compile_time_state_module_has_no_backend_dependencies():
    """The compile-time owner remains a pure compiler-state abstraction."""
    root = Path(__file__).resolve().parents[2]
    source = (root / "semantic" / "compile_time.py").read_text(encoding="utf-8")
    forbidden = (
        "import bpy",
        "from .values",
        "from .nodes",
        "from .runtime",
        "from .geometry_builder",
        "from .blender_ir_lowering",
        "from .blender_group_backend",
    )
    assert not any(token in source for token in forbidden)


def test_frontend_compile_time_modules_do_not_import_bpy():
    """Compile-time and semantic state separation does not spread Blender API access."""
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "semantic/compile_time.py",
        "semantic/consteval.py",
        "semantic/residualization.py",
        "semantic/evaluation_resolution.py",
        "semantic/body.py",
        "semantic/control_flow.py",
        "semantic/analysis.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "import bpy" not in source, relative
        assert "from bpy" not in source, relative


def test_compile_time_state_source_contracts_have_one_owner():
    """Compile-time ownership stays centralized without depending on compatibility comment text."""
    root = Path(__file__).resolve().parents[2]

    production_files = [
        path for path in root.rglob("*.py")
        if "tests" not in path.parts and ".git" not in path.parts
    ]
    direct_accesses = []
    for path in production_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "consts":
                # The property definition itself is not an Attribute AST node; any
                # remaining Attribute access is a production use of the old facade.
                direct_accesses.append((str(path.relative_to(root)), node.lineno))
    assert direct_accesses == []

    semantic_body_source = (root / "semantic/body.py").read_text(encoding="utf-8")
    control_source = (root / "semantic/control_flow.py").read_text(encoding="utf-8")
    assert "constants: dict[str, object]" not in semantic_body_source
    assert "CONTROL_FLOW_IR_LEGACY_CONSTANT_THREADING_COMPAT" not in control_source
    assert not (root / "statement_compiler.py").exists()
    assert not (root / "expression_compiler.py").exists()


def test_replace_accepts_state_and_snapshot_without_swapping_mapping():
    """Replacement is an in-place publication operation for either explicit state form."""
    backing = {"a": 1}
    state = CompileTimeState(backing, adopt_mapping=True)
    state.replace(CompileTimeState({"b": 2}))
    assert backing == {"b": 2}
    state.replace(CompileTimeSnapshot({"c": 3}))
    assert backing == {"c": 3}


def test_const_vector_has_one_compile_time_owner_class_identity():
    """consteval re-exports the compile-time owner's exact ConstVector class."""
    from NodeForge.semantic.compile_time import ConstVector as OwnedConstVector
    from NodeForge.semantic.consteval import ConstVector as ReexportedConstVector

    assert ReexportedConstVector is OwnedConstVector


def test_runtime_if_compile_time_merge_is_conservative_by_value_and_identity():
    """Only stable scalar equality and inherited object identity survive runtime joins."""
    from NodeForge.semantic.compile_time import ConstVector, merge_runtime_if_compile_time

    shared = ["same"]
    replaced = ["replacement"]
    base = CompileTimeState({
        "same_bool": True,
        "same_int": 3,
        "same_str": "x",
        "float": 1.0,
        "vector": ConstVector((1.0, 2.0, 3.0)),
        "alias": shared,
        "conflict": 1,
        "one_side": 4,
        "bool_int": True,
        "replaced": shared,
    })
    true_state = base.fork()
    false_state = base.fork()
    true_state.bind("conflict", 2)
    false_state.bind("conflict", 3)
    false_state.discard("one_side")
    false_state.bind("bool_int", 1)
    false_state.bind("replaced", replaced)

    merged = merge_runtime_if_compile_time(base.snapshot(), true_state, false_state)

    assert dict(merged.values) == {
        "same_bool": True,
        "same_int": 3,
        "same_str": "x",
        "alias": shared,
    }
    assert merged.values["alias"] is shared


def test_runtime_if_compile_time_merge_discards_unstable_or_unproven_value_categories():
    """Float/vector equality and one-sided/conflicting facts are not published at joins."""
    from NodeForge.semantic.compile_time import ConstVector, merge_runtime_if_compile_time

    base = CompileTimeState({"stable": 7, "signed_zero": 0.0})
    true_state = base.fork()
    false_state = base.fork()

    true_state.bind("float_equal", 1.0)
    false_state.bind("float_equal", 1.0)
    true_state.bind("signed_zero", 0.0)
    false_state.bind("signed_zero", -0.0)
    true_state.bind("vector_equal", ConstVector((1.0, 2.0, 3.0)))
    false_state.bind("vector_equal", ConstVector((1.0, 2.0, 3.0)))
    true_state.bind("vector_conflict", ConstVector((1.0, 2.0, 3.0)))
    false_state.bind("vector_conflict", ConstVector((1.0, 2.0, 4.0)))
    true_state.bind("one_sided", 9)
    true_state.bind("conflict", 1)
    false_state.bind("conflict", 2)

    merged = merge_runtime_if_compile_time(base.snapshot(), true_state, false_state)

    assert dict(merged.values) == {"stable": 7}
