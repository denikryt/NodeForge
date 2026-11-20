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
    return ()
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
                   semantic="def _result_types_foo(args): return ()\ndef _result_types_bar(args): return ()\n")
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
