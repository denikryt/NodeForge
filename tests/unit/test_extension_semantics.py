"""Semantic selection and ordinary Call IR transport for v2 extensions."""

import ast
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from NodeForge.call_resolution import CallableEnvironment
from NodeForge.compiler_identities import BindingId
from NodeForge.extension_registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.resolved_environment import PackageCallableExport, ResolvedPackageNamespace
from NodeForge.semantic_group import LibraryBinding, PackageNamespaceBinding
from NodeForge.semantic_analysis import SemanticEnvironment, analyze_expression
from NodeForge.semantic_ir import IRCall, IRCallableKind
from NodeForge.semantic_lowering import lower_analyzed_expression

pytestmark = pytest.mark.unit


def _registry(tmp_path: Path):
    """Build one two-overload extension registry for semantic selection tests."""
    (tmp_path / "interface.py").write_text(
        """
from typing import Annotated, overload
from NodeForge import Bool, EvaluationMode, Float, Int
EXTENSION_API = 2
EXTENSIONS = {"select": ".operations:select_node"}
@overload
def select(cond: Annotated[Bool, EvaluationMode.RUNTIME_ONLY], true: Annotated[Int, EvaluationMode.RUNTIME_ONLY], false: Annotated[Int, EvaluationMode.RUNTIME_ONLY]) -> Int: ...
@overload
def select(cond: Annotated[Bool, EvaluationMode.RUNTIME_ONLY], true: Annotated[Float, EvaluationMode.RUNTIME_ONLY], false: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def select(*args, **kwargs): ...
""",
        encoding="utf-8",
    )
    (tmp_path / "operations.py").write_text("def select_node(context, cond, true, false): return true\n", encoding="utf-8")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.demo", "math"), tmp_path))
    registry = ExtensionRegistry((session,))
    callable_id = next(iter(session.normalize_interface()[0]))
    return registry, callable_id


def _environment(registry, callable_id, branch_type):
    """Build one semantic environment exposing the test extension as a system."""
    runtime = {
        "cond": RuntimeBindingSymbol(BindingId("scope", 0), NFType.BOOL),
        "a": RuntimeBindingSymbol(BindingId("scope", 1), branch_type),
        "b": RuntimeBindingSymbol(BindingId("scope", 2), branch_type),
    }
    namespace = ResolvedPackageNamespace(
        "vendor.demo",
        "demo",
        "Demo",
        "1.0.0",
        {"select": PackageCallableExport("vendor.demo", "select", extension_callable_id=callable_id)},
    )
    callables = CallableEnvironment(
        callable_builtins=frozenset(),
        local_functions={},
        imported_functions={},
        package_namespaces={"demo": PackageNamespaceBinding("demo", namespace)},
    )
    return SemanticEnvironment(
        MappingProxyType(runtime),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        callables,
        extension_registry=registry,
    )


def test_exact_int_overload_survives_analyzed_call_and_ir(tmp_path):
    """An exact Int alternative must beat Float compatibility and survive into IR."""
    registry, callable_id = _registry(tmp_path)
    root = ast.parse("select(cond, a, b)", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, callable_id, NFType.INT))
    analyzed = analysis.facts[root].analyzed_call
    assert analyzed.extension_overload_index == 0
    assert analyzed.result.typ is NFType.INT
    program = lower_analyzed_expression(root, analysis)
    call = next(operation for operation in program.operations if isinstance(operation, IRCall))
    assert call.target.kind is IRCallableKind.EXTENSION
    assert call.target.extension_callable_id == callable_id
    assert call.target.extension_overload_index == 0
    assert call.results[0].typ is NFType.INT


def test_exact_float_overload_is_selected_without_order_tiebreak(tmp_path):
    """Float selection must use exact typing rather than declaration-order priority."""
    registry, callable_id = _registry(tmp_path)
    root = ast.parse("select(cond, a, b)", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, callable_id, NFType.FLOAT))
    analyzed = analysis.facts[root].analyzed_call
    assert analyzed.extension_overload_index == 1
    assert analyzed.result.typ is NFType.FLOAT


def test_mixed_source_interface_owner_is_rejected_before_source_preparation(tmp_path):
    """A source.nf + interface.py record must not be treated as a pure source callable."""
    class _Session:
        def prepare_library(self, **kwargs):
            raise AssertionError("mixed-owner rejection must run before source preparation")

    record = SimpleNamespace(
        namespace="functions",
        name="hybrid",
        package_id="vendor.demo",
        source_path=tmp_path / "source.nf",
        interface_path=tmp_path / "interface.py",
        module_path=None,
    )
    binding = LibraryBinding("functions", "hybrid", record)
    callables = CallableEnvironment(
        callable_builtins=frozenset(),
        local_functions={},
        imported_functions={"hybrid": binding},
    )
    environment = SemanticEnvironment(
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        callables,
        source_callable_session=_Session(),
        source_definition_owner="scope",
        source_owner_scope="scope",
        source_call_site_allocator=lambda _function_id: 0,
    )
    root = ast.parse("hybrid(1.0)", mode="eval").body
    with pytest.raises(Exception, match=r"unsupported mixed source\.nf \+ interface\.py package owner"):
        analyze_expression(root, environment)
