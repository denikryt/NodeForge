"""Pure contracts for compiler-owned compile-time state separation."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from types import MappingProxyType, ModuleType

import pytest

from NodeForge.compile_time import CompileTimeSnapshot, CompileTimeState
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


def _compiler_class(monkeypatch):
    """Import Compiler with a minimal bpy stub for compatibility-surface tests."""
    monkeypatch.setitem(sys.modules, "bpy", ModuleType("bpy"))
    return importlib.import_module("NodeForge.compiler").Compiler


def test_direct_compiler_constructor_adopts_consts_mapping(monkeypatch):
    """Direct Compiler callers retain the exact historical consts mapping identity."""
    from types import SimpleNamespace

    Compiler = _compiler_class(monkeypatch)
    module = importlib.import_module("NodeForge.compiler")
    environment = SimpleNamespace()
    monkeypatch.setattr(module, "_new_group_backend", lambda _environment: object())
    backing = {"seed": 4}
    compiler = Compiler(
        SimpleNamespace(name="Direct"),
        object(),
        consts=backing,
        resolved_environment=environment,
    )

    assert compiler.consts is backing
    assert compiler.compile_time.get("seed") == 4


def test_compiler_consts_facade_mutates_exact_compile_time_owner(monkeypatch):
    """The temporary .consts facade exposes one owner rather than a second state store."""
    Compiler = _compiler_class(monkeypatch)
    compiler = object.__new__(Compiler)
    backing = {"a": 1}
    compiler.compile_time = CompileTimeState(backing, adopt_mapping=True)

    assert compiler.consts is backing
    compiler.consts["b"] = 2
    assert compiler.compile_time.get("b") == 2

    owner = compiler.compile_time
    compiler.consts = {"c": 3}
    assert compiler.compile_time is owner
    assert backing == {"c": 3}


def test_compile_time_state_module_has_no_backend_dependencies():
    """The compile-time owner remains a pure compiler-state abstraction."""
    root = Path(__file__).resolve().parents[2]
    source = (root / "compile_time.py").read_text(encoding="utf-8")
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
        "compile_time.py",
        "consteval.py",
        "semantic_body.py",
        "semantic_control_flow.py",
        "semantic_analysis.py",
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

    semantic_body_source = (root / "semantic_body.py").read_text(encoding="utf-8")
    statement_source = (root / "statement_compiler.py").read_text(encoding="utf-8")
    expression_source = (root / "expression_compiler.py").read_text(encoding="utf-8")
    control_source = (root / "semantic_control_flow.py").read_text(encoding="utf-8")
    assert "constants: dict[str, object]" not in semantic_body_source
    assert "consts: dict" not in statement_source
    assert "COMPLETE_EXPRESSION_IR_CONSTANT_MIGRATION" not in expression_source
    assert "CONTROL_FLOW_IR_LEGACY_CONSTANT_THREADING_COMPAT" not in control_source


def test_replace_accepts_state_and_snapshot_without_swapping_mapping():
    """Replacement is an in-place publication operation for either explicit state form."""
    backing = {"a": 1}
    state = CompileTimeState(backing, adopt_mapping=True)
    state.replace(CompileTimeState({"b": 2}))
    assert backing == {"b": 2}
    state.replace(CompileTimeSnapshot({"c": 3}))
    assert backing == {"c": 3}
