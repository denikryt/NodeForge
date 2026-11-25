"""Dynamic extension declarations and owner-load preflight regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from NodeForge import DynamicResults
from NodeForge.errors import CompileError
from NodeForge.extensions.registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.extensions.semantics import ExtensionExecutionForm, classify_extension_execution

pytestmark = pytest.mark.unit


def _owner(root: Path, interface: str, **files: str):
    """Construct an isolated owner from its exact captured source files."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "interface.py").write_text(interface, encoding="utf-8")
    for module, source in files.items():
        (root / f"{module}.py").write_text(source, encoding="utf-8")
    return ExtensionOwnerSession(capture_owner_code_snapshot(("system", "dynamic.test", root.name), root))


_INTERFACE = '''
from typing import Annotated
from NodeForge import Float, DynamicResults, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
'''


def test_dynamic_declaration_preflight_is_resolver_body_lazy_and_backend_only(tmp_path):
    """Private semantic resolver is validated, but not executed at contract load."""
    owner = _owner(tmp_path, _INTERFACE,
                   semantic='''
CALLED = 0
def _result_types_foo(arguments):
    global CALLED
    CALLED += 1
    return {"value": arguments["value"].typ}
''', backend="def foo(context, value): return value\n")
    families, _ = owner.normalize_interface()
    spec = next(iter(families.values()))[0]
    assert spec.result.kind == "DYNAMIC_RESULTS"
    assert owner._phase_modules["SEMANTIC"]["semantic"].CALLED == 0
    assert classify_extension_execution(spec, None, has_implementation=True) is ExtensionExecutionForm.BACKEND_ONLY
    assert not hasattr(spec, "resolver_ref")
    assert not hasattr(DynamicResults, "__nodeforge_nf_type__")
    with pytest.raises(TypeError, match="annotation-only"):
        DynamicResults()


@pytest.mark.parametrize("semantic,match", [
    (None, "same-owner semantic.py"),
    ("OTHER = 123\n", "_result_types_foo"),
    ("_result_types_foo = 14\n", "callable"),
    ("from .backend import foo\ndef _result_types_foo(args): return ()\n", "SEMANTIC"),
])
def test_dynamic_owner_rejects_broken_resolver_on_load(tmp_path, semantic, match):
    """Malformed resolver contracts are rejected before callable publication."""
    kwargs = {"backend": "def foo(context, value): return value\n"}
    if semantic is not None:
        kwargs["semantic"] = semantic
    owner = _owner(tmp_path, _INTERFACE, **kwargs)
    with pytest.raises(CompileError, match=match):
        owner.normalize_interface()
    assert owner._families is None
    assert owner._implementation_modules == frozenset()


def test_dynamic_family_rejects_mixed_return_types(tmp_path):
    """The overload family cannot contain both dynamic and fixed results."""
    interface = '''
from typing import Annotated, overload
from NodeForge import Float, Int, DynamicResults, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
@overload
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
@overload
def foo(value: Annotated[Int, EvaluationMode.RUNTIME_ONLY]) -> Int: ...
def foo(value): ...
'''
    owner = _owner(tmp_path, interface, backend="def foo(context, value): return value\n",
                   semantic="def _result_types_foo(args): return ()\n")
    with pytest.raises(CompileError, match="mixes dynamic and fixed"):
        owner.normalize_interface()


def test_two_dynamic_exports_can_share_same_semantic_module(tmp_path):
    """Derived exports use each public name and one preflighted semantic generation."""
    interface = _INTERFACE.replace('"foo": ".backend:foo"', '"foo": ".backend:foo", "bar": ".backend:bar"')
    interface += 'def bar(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...\n'
    owner = _owner(tmp_path, interface,
                   backend="def foo(context, value): return value\ndef bar(context, value): return value\n",
                   semantic="def _result_types_foo(args): return {'value': args['value'].typ}\ndef _result_types_bar(args): return {'value': args['value'].typ}\n")
    registry = ExtensionRegistry((owner,))
    assert {cid.name for cid in registry._families} == {"foo", "bar"}
    assert set(owner._phase_modules["SEMANTIC"]) == {"semantic"}


def test_non_exact_dynamic_marker_does_not_opt_in(tmp_path):
    """Subclassed result markers have no special authority at contract load."""
    interface = _INTERFACE.replace('EXTENSION_API = 2', 'class Other(DynamicResults): pass\nEXTENSION_API = 2')
    interface = interface.replace('-> DynamicResults:', '-> Other:')
    owner = _owner(tmp_path, interface, backend="def foo(context, value): return value\n",
                   semantic="def _result_types_foo(args): return ()\n")
    with pytest.raises(CompileError, match="Unsupported"):
        owner.normalize_interface()


def _dynamic_semantic_environment(owner: ExtensionOwnerSession):
    """Expose a synthetic dynamic owner with already-typed frontend runtime bindings."""
    import ast
    from types import MappingProxyType
    from NodeForge.compiler_identities import BindingId
    from NodeForge.nf_types import NFType
    from NodeForge.resolved_environment import PackageCallableExport, ResolvedPackageNamespace
    from NodeForge.semantic.call_resolution import CallableEnvironment
    from NodeForge.semantic.source_bindings import PackageNamespaceBinding
    from NodeForge.semantic.analysis import SemanticEnvironment
    from NodeForge.semantic.runtime_bindings import RuntimeBindingSymbol

    registry = ExtensionRegistry((owner,))
    cid = next(iter(owner.normalize_interface()[0]))
    namespace = ResolvedPackageNamespace("dynamic.test", "dyn", "Dynamic", "1", {
        cid.name: PackageCallableExport("dynamic.test", cid.name, extension_callable_id=cid),
    })
    types = {
        "geo": NFType.GEOMETRY, "f": NFType.FLOAT, "v": NFType.VECTOR,
        "i": NFType.INT, "b": NFType.BOOL, "s": NFType.STRING,
    }
    variables = {name: RuntimeBindingSymbol(BindingId("scope", index), typ)
                 for index, (name, typ) in enumerate(types.items())}
    calls = CallableEnvironment(frozenset(), {}, {},
                                package_namespaces={"dyn": PackageNamespaceBinding("dyn", namespace)})
    env = SemanticEnvironment(MappingProxyType(variables), MappingProxyType({}),
                              MappingProxyType({}), MappingProxyType({}), calls,
                              extension_registry=registry)
    return env, cid


_DYNAMIC_INTERFACE = '''
from typing import Annotated
from NodeForge import Bool, DynamicResults, EvaluationMode, Float, Geometry, Int, String, Vector
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(geometry: Annotated[Geometry, EvaluationMode.RUNTIME_ONLY],
        *values: Annotated[Float | Int | Vector, EvaluationMode.RUNTIME_ONLY],
        selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = True,
        domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = "POINT") -> DynamicResults: ...
'''
_DYNAMIC_SEMANTIC = '''
from NodeForge.nf_types import NFType
from NodeForge.extension_result_api import ResultRuntimeArg, ResultStaticArg
CALLED = 0
LAST_ARGUMENTS = None
def _result_types_foo(arguments):
    global CALLED, LAST_ARGUMENTS
    CALLED += 1
    LAST_ARGUMENTS = arguments
    assert isinstance(arguments['geometry'], ResultRuntimeArg)
    assert isinstance(arguments['selection'], (ResultRuntimeArg, ResultStaticArg))
    assert isinstance(arguments['domain'], ResultStaticArg)
    return {"geometry": NFType.GEOMETRY, "selection": NFType.BOOL, **{f"value_{i}": arg.typ for i, arg in enumerate(arguments['values'])}}
'''


def test_resolver_uses_call_local_actual_types_and_exact_ir(tmp_path):
    """Two invocations share a declaration but own different concrete typed IR tuples."""
    import ast
    from NodeForge.nf_types import NFType
    from NodeForge.semantic.analysis import analyze_expression
    from NodeForge.semantic.ir import IRCall
    from NodeForge.semantic.lowering import lower_analyzed_expression
    owner = _owner(tmp_path, _DYNAMIC_INTERFACE, backend="def foo(*args, **kw): pass\n",
                   semantic=_DYNAMIC_SEMANTIC)
    env, _cid = _dynamic_semantic_environment(owner)
    module = owner._phase_modules["SEMANTIC"]["semantic"]
    assert module.CALLED == 0
    for text, expected in [
        ("foo(geo, f)", (NFType.GEOMETRY, NFType.BOOL, NFType.FLOAT)),
        ("foo(geo, v, i, domain='point')", (NFType.GEOMETRY, NFType.BOOL, NFType.VECTOR, NFType.INT)),
    ]:
        root = ast.parse(text, mode="eval").body
        analysis = analyze_expression(root, env)
        analyzed = analysis.facts[root].analyzed_call
        assert tuple(typ for _, typ in analyzed.result.items) == expected
        program = lower_analyzed_expression(root, analysis)
        call = next(item for item in program.operations if isinstance(item, IRCall))
        assert tuple(result.typ for result in call.results) == expected
        assert len(call.arguments) == (2 if "v, i" in text else 2) + (0 if "v, i" not in text else 1)
    assert module.CALLED == 2
    assert module.LAST_ARGUMENTS["domain"].value == "point"
    assert module.LAST_ARGUMENTS["selection"].value is True
    assert type(module.LAST_ARGUMENTS["values"]) is tuple
    assert not hasattr(module.LAST_ARGUMENTS, "__setitem__")
    with pytest.raises(TypeError):
        module.LAST_ARGUMENTS["domain"] = None


def test_empty_dynamic_values_still_resolve_a_named_result(tmp_path):
    """A one-element resolver result does not silently become a scalar expression."""
    import ast
    from NodeForge.nf_types import NFType
    from NodeForge.semantic.analysis import analyze_expression
    owner = _owner(tmp_path, _DYNAMIC_INTERFACE, backend="def foo(*args, **kw): pass\n",
                   semantic="from NodeForge.nf_types import NFType\ndef _result_types_foo(args): return {'value': NFType.FLOAT}\n")
    env, _ = _dynamic_semantic_environment(owner)
    root = ast.parse("foo(geo)", mode="eval").body
    result = analyze_expression(root, env).facts[root].analyzed_call.result
    assert tuple(typ for _, typ in result.items) == (NFType.FLOAT,)


@pytest.mark.parametrize("expression", ["[]", "()", "(NFType.FLOAT,)", "[NFType.FLOAT]", "{}", "{'': NFType.FLOAT}", "None", "{NFType.FLOAT}",
                                          "(NFType.FLOAT, 42)", "'FLOAT'"])
def test_dynamic_result_resolver_rejects_invalid_shapes_during_analysis(tmp_path, expression):
    """All malformed resolver outputs fail before typed Call IR or Blender creation."""
    import ast
    from NodeForge.semantic.analysis import analyze_expression
    owner = _owner(tmp_path, _DYNAMIC_INTERFACE, backend="def foo(*args, **kw): pass\n",
                   semantic=f"from NodeForge.nf_types import NFType\ndef _result_types_foo(args): return {expression}\n")
    env, _ = _dynamic_semantic_environment(owner)
    root = ast.parse("foo(geo, f)", mode="eval").body
    with pytest.raises(CompileError, match="dynamic|Dynamic|resolver"):
        analyze_expression(root, env)


def test_snapshot_owned_resolver_remains_unchanged_when_source_changes(tmp_path):
    """Each captured generation uses its own semantic.py body and fingerprint."""
    import ast
    from NodeForge.nf_types import NFType
    from NodeForge.semantic.analysis import analyze_expression
    semantic = "from NodeForge.nf_types import NFType\ndef _result_types_foo(args): return {\"value\": NFType.FLOAT}\n"
    old = _owner(tmp_path, _DYNAMIC_INTERFACE, backend="def foo(*args, **kw): pass\n", semantic=semantic)
    old_env, _ = _dynamic_semantic_environment(old)
    expression = ast.parse("foo(geo, f)", mode="eval").body
    assert tuple(typ for _, typ in analyze_expression(expression, old_env).facts[expression].analyzed_call.result.items) == (NFType.FLOAT,)
    (tmp_path / "semantic.py").write_text(semantic.replace("NFType.FLOAT", "NFType.INT"), encoding="utf-8")
    new = ExtensionOwnerSession(capture_owner_code_snapshot(old.snapshot.owner_key, tmp_path))
    new_env, _ = _dynamic_semantic_environment(new)
    assert old.snapshot.fingerprint != new.snapshot.fingerprint
    assert tuple(typ for _, typ in analyze_expression(expression, old_env).facts[expression].analyzed_call.result.items) == (NFType.FLOAT,)
    assert tuple(typ for _, typ in analyze_expression(expression, new_env).facts[expression].analyzed_call.result.items) == (NFType.INT,)


def test_overloads_share_one_resolver_with_selected_actual_type(tmp_path):
    """One derived resolver sees the selected typed alternative, not its union."""
    import ast
    from NodeForge.nf_types import NFType
    from NodeForge.semantic.analysis import analyze_expression
    interface = '''
from typing import Annotated, overload
from NodeForge import Float, Vector, DynamicResults, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
@overload
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
@overload
def foo(value: Annotated[Vector, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
def foo(value): ...
'''
    owner = _owner(tmp_path, interface, backend="def foo(*args): pass\n",
                   semantic="def _result_types_foo(args): return {'value': args['value'].typ}\n")
    env, _ = _dynamic_semantic_environment(owner)
    for source, index, typ in [("foo(f)", 0, NFType.FLOAT), ("foo(v)", 1, NFType.VECTOR)]:
        root = ast.parse(source, mode="eval").body
        call = analyze_expression(root, env).facts[root].analyzed_call
        assert call.extension_overload_index == index
        assert tuple(typ for _, typ in call.result.items) == (typ,)


def test_public_semantic_state_and_private_result_resolver_coexist(tmp_path):
    """A result-only helper neither replaces nor double-executes semantic state."""
    import ast
    from NodeForge.nf_types import NFType
    from NodeForge.semantic.analysis import analyze_expression
    from NodeForge.extensions.semantics import ExtensionExecutionForm
    interface = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import DynamicResults, Float, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
@dataclass(frozen=True)
class _State:
    value: Float
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
'''
    semantic = '''
from .interface import _State
from NodeForge.nf_types import NFType
CALLS = 0
RESOLVES = 0
def foo(value) -> _State:
    global CALLS
    CALLS += 1
    return _State(value)
def _result_types_foo(args):
    global RESOLVES
    RESOLVES += 1
    return {'value': args['value'].typ}
'''
    owner = _owner(tmp_path, interface, semantic=semantic,
                   backend="def foo(context, state): return None\n")
    env, cid = _dynamic_semantic_environment(owner)
    spec = owner.normalize_interface()[0][cid][0]
    state = owner.semantic_return_type(cid)
    assert classify_extension_execution(spec, state, has_implementation=True) is ExtensionExecutionForm.SEMANTIC_THEN_BACKEND
    root = ast.parse("foo(f)", mode="eval").body
    result = analyze_expression(root, env).facts[root].analyzed_call
    assert tuple(typ for _, typ in result.result.items) == (NFType.FLOAT,)
    sem = owner._phase_modules["SEMANTIC"]["semantic"]
    assert sem.CALLS == sem.RESOLVES == 1
    assert result.extension_state_type == state


def test_dynamic_editor_signature_reports_unresolved_named_result_without_fabricated_fields(tmp_path):
    """Editor completion shows the marker while leaving per-call arity unspecified."""
    from NodeForge.editor.intelligence import _extension_signatures, _return_preview
    owner = _owner(tmp_path, _DYNAMIC_INTERFACE, semantic=_DYNAMIC_SEMANTIC,
                   backend="def foo(*args, **kw): pass\n")
    specs = next(iter(owner.normalize_interface()[0].values()))
    signature, = _extension_signatures("foo", specs)
    assert signature.result_kind == "dynamic"
    assert not signature.outputs
    assert "unresolved" in _return_preview(signature)
    assert len(signature.parameters) == 4


def test_dynamic_resolver_rejects_runtime_domain_from_same_binding_path(tmp_path):
    """Existing argument-mode checks reject a runtime-only static parameter early."""
    import ast
    from NodeForge.semantic.analysis import analyze_expression
    owner = _owner(tmp_path, _DYNAMIC_INTERFACE, backend="def foo(*args): pass\n",
                   semantic=_DYNAMIC_SEMANTIC)
    env, _ = _dynamic_semantic_environment(owner)
    root = ast.parse("foo(geo, f, domain=s)", mode="eval").body
    with pytest.raises(CompileError, match="compile time"):
        analyze_expression(root, env)


def test_call_specific_tuple_index_rejects_out_of_range(tmp_path):
    """Resolved arity participates in ordinary tuple indexing semantics."""
    import ast
    from NodeForge.semantic.analysis import analyze_expression
    owner = _owner(tmp_path, _DYNAMIC_INTERFACE, backend="def foo(*args): pass\n",
                   semantic=_DYNAMIC_SEMANTIC)
    env, _ = _dynamic_semantic_environment(owner)
    good = ast.parse('foo(geo, f)["value_0"]', mode="eval").body
    assert analyze_expression(good, env).facts[good].result_shape is not None
    bad = ast.parse('foo(geo, f)["missing"]', mode="eval").body
    with pytest.raises(CompileError):
        analyze_expression(bad, env)


def test_optional_backend_dynamic_resolver_sees_omission_separately_from_values(tmp_path):
    """A resolver gets None only for an omitted parameter, not a synthetic typed view."""
    import ast
    from NodeForge.semantic.analysis import analyze_expression
    from NodeForge.extension_result_api import ResultRuntimeArg, ResultStaticArg
    from NodeForge.nf_types import NFType

    source = _DYNAMIC_INTERFACE.replace(
        'domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = "POINT"',
        'domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = None',
    )
    semantic = '''from NodeForge.nf_types import NFType
SEEN = []
def _result_types_foo(arguments):
    SEEN.append(dict(arguments))
    return {"geometry": NFType.GEOMETRY}
'''
    owner = _owner(tmp_path, source, backend="def foo(*a, **kw): pass\n", semantic=semantic)
    env, _ = _dynamic_semantic_environment(owner)
    for expression in ("foo(geo)", "foo(geo, selection=b, domain='POINT')"):
        node = ast.parse(expression, mode="eval").body
        assert tuple(typ for _, typ in analyze_expression(node, env).facts[node].analyzed_call.result.items) == (NFType.GEOMETRY,)
    first, second = owner._phase_modules["SEMANTIC"]["semantic"].SEEN
    assert first["domain"] is None
    assert isinstance(first["selection"], ResultStaticArg) and first["selection"].value is True
    assert isinstance(second["domain"], ResultStaticArg) and second["domain"].value == "POINT"
    assert isinstance(second["selection"], ResultRuntimeArg)



def test_semantic_dynamic_resolver_reads_optional_none_once_per_call(tmp_path):
    """A semantic-then-backend result resolver handles omitted direct NF inputs."""
    from NodeForge.tests.unit.test_extension_optional_defaults import (
        _SEMANTIC_BACKEND_INTERFACE, _SEMANTIC_BACKEND, _analyzed, _owner,
    )
    from NodeForge.extension_result_api import ResultRuntimeArg
    owner = _owner(tmp_path, _SEMANTIC_BACKEND_INTERFACE, semantic=_SEMANTIC_BACKEND)
    for expression in ("sample(f)", "sample(f, optional=i)"):
        _analyzed(owner, expression)
    semantic = owner._phase_modules["SEMANTIC"]["semantic"]
    assert len(semantic.RESOLVED) == len(semantic.SEEN) == 2
    assert semantic.RESOLVED[0]["optional"] is None
    assert isinstance(semantic.RESOLVED[1]["optional"], ResultRuntimeArg)
