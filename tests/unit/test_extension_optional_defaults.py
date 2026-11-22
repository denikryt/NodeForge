"""Contract and analysis regressions for omitted Extension API v2 typed arguments."""

import inspect
from pathlib import Path

import pytest

from NodeForge.errors import CompileError
from NodeForge.extensions.registry import ExtensionOwnerSession, capture_owner_code_snapshot
from NodeForge.evaluation_modes import EvaluationMode

pytestmark = pytest.mark.unit


def _owner(tmp_path: Path, interface: str, *, semantic: str | None = None):
    """Capture a synthetic extension owner in the same source layout as packages."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "interface.py").write_text(interface, encoding="utf-8")
    (tmp_path / "backend.py").write_text("def sample(*args, **kwargs): pass\n", encoding="utf-8")
    if semantic is not None:
        (tmp_path / "semantic.py").write_text(semantic, encoding="utf-8")
    return ExtensionOwnerSession(capture_owner_code_snapshot(("system", "dynamic.test", tmp_path.name), tmp_path))


@pytest.mark.parametrize("mode", list(EvaluationMode))
def test_declaration_accepts_omitted_typed_union_without_type(tmp_path, mode):
    """None is a declaration-level omission marker, including for runtime-only unions."""
    source = f'''from typing import Annotated
from NodeForge import Float, Int, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {{"sample": ".backend:sample"}}
def sample(required: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
           *, optional: Annotated[Float | Int, EvaluationMode.{mode.name}] = None) -> Float: ...
'''
    owner = _owner(tmp_path, source)
    spec = next(iter(owner.normalize_interface()[0].values()))[0]
    assert spec.parameters[0].default is inspect.Parameter.empty
    assert spec.parameters[1].default is None and spec.parameters[1].default_type is None
    bound = spec.python_signature().bind(object())
    bound.apply_defaults()
    assert bound.arguments["optional"] is None
    with pytest.raises(TypeError):
        spec.python_signature().bind()


@pytest.mark.parametrize("annotation, default, error", [
    ("Annotated[Float, EvaluationMode.RUNTIME_ONLY]", "1.0", "RUNTIME_ONLY"),
    ("Annotated[Float | None, EvaluationMode.COMPILE_TIME_ONLY]", "None", "union members"),
    ("Annotated[Any, EvaluationMode.COMPILE_TIME_ONLY]", "None", "Any"),
    ("Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY]", "'wrong'", "default"),
])
def test_invalid_optional_declarations_stay_rejected(tmp_path, annotation, default, error):
    """Only an exact Python None default can bypass typed-value normalization."""
    source = f'''from typing import Annotated, Any
from NodeForge import Float, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {{"sample": ".backend:sample"}}
def sample(value: {annotation} = {default}) -> Float: ...
'''
    with pytest.raises(CompileError, match=error):
        _owner(tmp_path, source).normalize_interface()


_BACKEND_INTERFACE = '''from typing import Annotated
from NodeForge import Float, Int, Geometry, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"sample": ".backend:sample"}
def sample(geometry: Annotated[Geometry, EvaluationMode.RUNTIME_ONLY],
           *, optional: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
           required_mode: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY] = 1.5,
           runtime_optional: Annotated[Float, EvaluationMode.RUNTIME_ONLY] = None) -> Geometry: ...
'''


def _analyzed(owner, source):
    """Type one synthetic package call with the actual compiler runtime bindings."""
    import ast
    from NodeForge.tests.unit.test_extension_dynamic_results import _dynamic_semantic_environment
    from NodeForge.semantic.analysis import analyze_expression
    from NodeForge.semantic.lowering import lower_analyzed_expression
    env, _ = _dynamic_semantic_environment(owner)
    expression = ast.parse(source, mode="eval").body
    analyzed = analyze_expression(expression, env)
    return analyzed.facts[expression].analyzed_call, lower_analyzed_expression(expression, analyzed)


def test_backend_omission_keeps_indexed_ir_and_concrete_default(tmp_path):
    """Only omission carries a None value; concrete defaults remain typed static IR."""
    from NodeForge.semantic.ir import IRCall
    owner = _owner(tmp_path, _BACKEND_INTERFACE)
    analyzed, program = _analyzed(owner, "sample(geo)")
    assert [(a.parameter_index, a.value) for a in analyzed.static_operands] == [
        (1, None), (2, 1.5), (3, None)]
    assert len(analyzed.runtime_operands) == 1
    call, = [op for op in program.operations if isinstance(op, IRCall)]
    assert [(a.parameter_index, a.value) for a in call.static_arguments] == [
        (1, None), (2, 1.5), (3, None)]
    assert [a.parameter_index for a in call.arguments] == [0]


def test_explicit_backend_values_obey_mode_and_keep_runtime_slots(tmp_path):
    """Supplied constants and runtime arguments retain their normal typed carriers."""
    owner = _owner(tmp_path, _BACKEND_INTERFACE)
    call, _ = _analyzed(owner, "sample(geo, runtime_optional=f, optional=i)")
    assert [(a.parameter_index, a.value) for a in call.static_operands] == [(2, 1.5)]
    assert sorted(a.parameter_index for a in call.runtime_operands) == [0, 1, 3]
    call, _ = _analyzed(owner, "sample(geo, optional=10)")
    assert [(a.parameter_index, a.value) for a in call.static_operands] == [
        (1, 10), (2, 1.5), (3, None)]


@pytest.mark.parametrize("source,match", [
    ("sample()", "signature"),
    ("sample(geo, missing=10)", "signature"),
    ("sample(geo, optional=1, optional=2)", "multiple values"),
    ("sample(geo, optional=None)", "Only numeric, boolean and string constants"),
    ("sample(geo, required_mode=f)", "compile time"),
    ("sample(geo, runtime_optional=v)", "types do not match"),
    ("sample(geo, **{})", "does not support caller-side"),
    ("sample(geo, *[f])", "does not support caller-side"),
])
def test_backend_omission_does_not_admit_invalid_calls(tmp_path, source, match):
    """Omission must not bypass caller binding or supplied-argument validation."""
    owner = _owner(tmp_path, _BACKEND_INTERFACE)
    with pytest.raises(CompileError, match=match):
        _analyzed(owner, source)


def test_overload_omitted_input_gives_no_type_evidence(tmp_path):
    """Equal omitted overloads remain ambiguous; explicit types select alternatives."""
    source = '''from typing import Annotated, overload
from NodeForge import Float, Int, Geometry, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"sample": ".backend:sample"}
@overload
def sample(geometry: Annotated[Geometry, EvaluationMode.RUNTIME_ONLY],
           *, option: Annotated[Int, EvaluationMode.RUNTIME_ONLY] = None) -> Int: ...
@overload
def sample(geometry: Annotated[Geometry, EvaluationMode.RUNTIME_ONLY],
           *, option: Annotated[Float, EvaluationMode.RUNTIME_ONLY] = None) -> Float: ...
def sample(*args, **kwargs): ...
'''
    owner = _owner(tmp_path, source)
    with pytest.raises(CompileError, match="ambiguous"):
        _analyzed(owner, "sample(geo)")
    for expression, index in [("sample(geo, option=i)", 0), ("sample(option=f, geometry=geo)", 1)]:
        analyzed, _ = _analyzed(owner, expression)
        assert analyzed.extension_overload_index == index


def test_backend_resolver_omission_is_plain_none(tmp_path):
    """Resolver receives an untyped omitted slot and typed supplied/default arguments."""
    from NodeForge.nf_types import NFType
    from NodeForge.extension_result_api import ResultRuntimeArg, ResultStaticArg
    interface = _BACKEND_INTERFACE.replace("-> Geometry: ...", "-> DynamicResults: ...")
    interface = interface.replace("from NodeForge import Float,", "from NodeForge import DynamicResults, Float,")
    semantic = '''from NodeForge.nf_types import NFType
from NodeForge.extension_result_api import ResultRuntimeArg, ResultStaticArg
SEEN = []
def _result_types_sample(arguments):
    assert not hasattr(arguments, "__setitem__")
    SEEN.append(dict(arguments))
    return (NFType.GEOMETRY, NFType.FLOAT)
'''
    owner = _owner(tmp_path, interface, semantic=semantic)
    call, _ = _analyzed(owner, "sample(geo)")
    assert call.result.types == (NFType.GEOMETRY, NFType.FLOAT)
    call, _ = _analyzed(owner, "sample(geo, optional=i, runtime_optional=f)")
    assert call.result.types == (NFType.GEOMETRY, NFType.FLOAT)
    seen = owner._phase_modules["SEMANTIC"]["semantic"].SEEN
    assert len(seen) == 2
    assert seen[0]["optional"] is None and seen[0]["runtime_optional"] is None
    assert isinstance(seen[0]["required_mode"], ResultStaticArg)
    assert seen[0]["required_mode"].value == 1.5
    assert isinstance(seen[1]["optional"], ResultRuntimeArg)
    assert isinstance(seen[1]["runtime_optional"], ResultRuntimeArg)
