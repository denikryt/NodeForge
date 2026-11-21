"""Semantic-only extension mode resolution and call-local result shape preservation."""

import ast
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from NodeForge.compiler_identities import BindingId, ExtensionCallSiteId
from NodeForge.errors import CompileError
from NodeForge.extensions.registry import (
    ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot,
)
from NodeForge.nf_types import NFType
from NodeForge.resolved_environment import PackageCallableExport, ResolvedPackageNamespace
from NodeForge.semantic.analysis import SemanticEnvironment, analyze_expression
from NodeForge.semantic.body import _BodyIdentityAllocator
from NodeForge.semantic.call_resolution import CallableEnvironment, TupleCallResult
from NodeForge.semantic.ir import (
    IRCall, IRExtensionMaterializationMode, IRExtensionMaterialization,
    IRCallableKind, IRCallableTarget, IRValue,
)
from NodeForge.semantic.lowering import lower_analyzed_expression
from NodeForge.semantic.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic.source_bindings import PackageNamespaceBinding


def _synthetic(tmp_path, *, interface=None, semantic=None, backend=None):
    """Prepare exact call contracts in a private extension snapshot, not Blender."""
    owner_path = tmp_path / "synthetic"
    owner_path.mkdir()
    (owner_path / "interface.py").write_text(interface or '''
from typing import Annotated
from NodeForge import DynamicResults, EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
''')
    (owner_path / "semantic.py").write_text(semantic or '''
from NodeForge.nf_types import NFType
CALLED = 0
def _result_types_foo(arguments):
    global CALLED
    CALLED += 1
    return (NFType.FLOAT,)
''')
    (owner_path / "backend.py").write_text(backend or "def foo(context, value): return value\n")
    owner = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "fixture.extension", "one"), owner_path))
    registry = ExtensionRegistry((owner,))
    ids = tuple(owner.normalize_interface()[0])
    cid = ids[0]
    namespace = ResolvedPackageNamespace("fixture.extension", "syn", "Fixture", "1", {
        item.name: PackageCallableExport("fixture.extension", item.name, extension_callable_id=item)
        for item in ids
    })
    bindings = {"x": RuntimeBindingSymbol(BindingId("scope", 0), NFType.FLOAT),
                "flag": RuntimeBindingSymbol(BindingId("scope", 1), NFType.BOOL)}
    calls = CallableEnvironment(frozenset(), {}, {},
                                package_namespaces={"syn": PackageNamespaceBinding("syn", namespace)})
    env = SemanticEnvironment(MappingProxyType(bindings), MappingProxyType({}),
                              MappingProxyType({}), MappingProxyType({}), calls,
                              extension_registry=registry)
    # The same allocator used by body lowering also counts INLINE calls.
    allocator = _BodyIdentityAllocator("source", "source", 0, {}, {}, {}, {},
                                       physical_owner_scope="physical-owner")
    return replace(env, extension_call_site_allocator=allocator.allocate_extension_call_site_id,
                   physical_owner_scope="physical-owner"), cid, owner


def _ir(source, env):
    root = ast.parse(source, mode="eval").body
    analysis = analyze_expression(root, env)
    return analysis.facts[root].analyzed_call, next(
        op for op in lower_analyzed_expression(root, analysis).operations if isinstance(op, IRCall)
    )


def test_default_true_and_false_freeze_one_physical_mode_and_singleton_tuple(tmp_path):
    """The grouped child receives the already resolved dynamic tuple, not a new resolver call."""
    env, cid, owner = _synthetic(tmp_path)
    materializations = []
    for source, mode in (("syn.foo(x)", "INLINE"),
                         ("syn.foo(x, __inline__=True)", "INLINE"),
                         ("syn.foo(x, __inline__=False)", "GROUPED")):
        analyzed, call = _ir(source, env)
        assert call.extension_materialization.mode.value == mode
        assert call.extension_materialization.call_site.callee == cid
        assert analyzed.result.types == (NFType.FLOAT,)
        assert isinstance(analyzed.result, TupleCallResult)
        assert tuple(result.typ for result in call.results) == (NFType.FLOAT,)
        materializations.append(call.extension_materialization.call_site)
    assert [item.ordinal for item in materializations] == [0, 1, 2]
    assert owner._phase_modules["SEMANTIC"]["semantic"].CALLED == 3


@pytest.mark.parametrize("source,expected", [
    ("syn.foo(x, __inline__=flag)", "compile-time Bool"),
    ("syn.foo(x, __inline__=0)", "compile-time Bool"),
    ("syn.foo(x, __inline__=False, __inline__=True)", "duplicate __inline__"),
    ("syn.foo(x, __unique__=False)", "does not support __unique__"),
    ("syn.foo(x, **{})", "caller-side \\*\\* argument"),
])
def test_extension_invalid_modes_reject_before_physical_execution(tmp_path, source, expected):
    """Runtime/Int/duplicated/unpacked modifiers cannot bypass the semantic boundary."""
    env, _cid, owner = _synthetic(tmp_path)
    with pytest.raises(CompileError, match=expected):
        _ir(source, env)
    assert owner._phase_modules["SEMANTIC"]["semantic"].CALLED == 0


def test_semantic_only_extension_rejects_inline_and_unique(tmp_path):
    """A semantic-value producer has no physical materialization mode."""
    interface = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import Float, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"foo": None}
@dataclass(frozen=True)
class Part:
    value: Float
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
'''
    env, _cid, _owner = _synthetic(
        tmp_path, interface=interface,
        semantic="from .interface import Part\ndef foo(value): return Part(value)\n",
    )
    for source in ("syn.foo(x, __inline__=False)", "syn.foo(x, __unique__=False)"):
        with pytest.raises(CompileError):
            _ir(source, env)


def test_category_specific_ir_rejects_extension_materialization_on_source_call():
    """A FunctionId call cannot accidentally accept extension occurrence metadata."""
    from NodeForge.compiler_identities import local_function_id
    fid = local_function_id("scope", "f", "")
    with pytest.raises(ValueError, match="only extension calls"):
        IRCall(
            results=(IRValue(0, NFType.FLOAT),), depth=0,
            target=IRCallableTarget(IRCallableKind.SOURCE_FUNCTION, "f", function_id=fid),
            arguments=(),
            extension_materialization=IRExtensionMaterialization(IRExtensionMaterializationMode.INLINE),
        )


def test_semantic_then_backend_uses_one_compacted_runtime_dependency(tmp_path):
    """Grouped-mode metadata does not change the semantic-state transport contract."""
    interface = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import Float, EvaluationMode
EXTENSION_API = 2
@dataclass(frozen=True)
class Part:
    value: Float
@dataclass(frozen=True)
class _BuildSpec:
    part: Part
EXTENSIONS = {"make": None, "consume": ".backend:consume"}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def consume(part: Part) -> Float: ...
'''
    semantic = '''
from .interface import Part, _BuildSpec
def make(value) -> Part:
    return Part(value)
def consume(part) -> _BuildSpec:
    return _BuildSpec(part)
'''
    env, _cid, _owner = _synthetic(
        tmp_path, interface=interface, semantic=semantic,
        backend="def consume(context, state): return state\n",
    )
    analyzed, call = _ir("syn.consume(syn.make(x), __inline__=False)", env)
    assert analyzed.extension_state_type is not None
    assert call.extension_materialization.mode is IRExtensionMaterializationMode.GROUPED
    assert len(call.arguments) == 1
    assert call.arguments[0].parameter_index is None
    assert call.arguments[0].value.typ is NFType.FLOAT
    assert call.results[0].typ is NFType.FLOAT


def test_extension_interface_reserves_inline_parameter(tmp_path):
    """A public declaration cannot shadow the compiler-reserved materialization flag."""
    from NodeForge.extensions.registry import ExtensionOwnerSession, capture_owner_code_snapshot
    root = tmp_path / "reserved"
    root.mkdir()
    (root / "interface.py").write_text('''
from typing import Annotated
from NodeForge import Float, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(__inline__: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
''')
    (root / "backend.py").write_text("def foo(context, value): return value\n")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "reserved", "one"), root))
    with pytest.raises(CompileError, match="reserved by the compiler"):
        session.normalize_interface()
