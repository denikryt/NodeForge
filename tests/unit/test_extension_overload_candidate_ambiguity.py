"""Candidate-local Rotation/Vector ambiguity in backend extension overload resolution."""

import ast
from pathlib import Path
from types import MappingProxyType

import pytest

from NodeForge.errors import CompileError
from NodeForge.extensions.registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.nf_types import NFType
from NodeForge.resolved_environment import PackageCallableExport, ResolvedPackageNamespace
from NodeForge.semantic.analysis import SemanticEnvironment, analyze_expression
from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.semantic.source_bindings import PackageNamespaceBinding

pytestmark = pytest.mark.unit


def _pick_registry(tmp_path: Path, *, reverse: bool = False, only_union: bool = False):
    """Register two candidates with an ambiguous union and an unambiguous Rotation."""
    ambiguous = '''@overload
def pick(value: Annotated[Rotation | Vector, EvaluationMode.COMPILE_TIME_ONLY],
             mode: Annotated[Int, EvaluationMode.COMPILE_TIME_ONLY]) -> Rotation: ...'''
    rotation = '''@overload
def pick(value: Annotated[Rotation, EvaluationMode.COMPILE_TIME_ONLY],
             mode: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY]) -> Rotation: ...'''
    declarations = ((ambiguous.replace('@overload\n', '') ,) if only_union
                    else ((rotation, ambiguous) if reverse else (ambiguous, rotation)))
    (tmp_path / 'interface.py').write_text(
        'from typing import Annotated, overload\n'
        'from NodeForge import Rotation, Vector, Int, Float, EvaluationMode\n'
        'EXTENSION_API = 2\nEXTENSIONS = {"pick": ".operations:pick"}\n'
        + '\n'.join(declarations) + ('\n' if only_union else '\ndef pick(*args, **kwargs): ...\n'), encoding='utf-8'
    )
    (tmp_path / 'operations.py').write_text('def pick(context, value, mode): return None\n', encoding='utf-8')
    session = ExtensionOwnerSession(capture_owner_code_snapshot(('system', 'vendor.demo', 'candidate'), tmp_path))
    registry = ExtensionRegistry((session,))
    callable_id = next(iter(session.normalize_interface()[0]))
    namespace = ResolvedPackageNamespace(
        'vendor.demo', 'demo', 'Demo', '1.0.0',
        {'pick': PackageCallableExport('vendor.demo', 'pick', extension_callable_id=callable_id)},
    )
    callables = CallableEnvironment(
        callable_builtins=frozenset(), local_functions={}, imported_functions={},
        package_namespaces={'demo': PackageNamespaceBinding('demo', namespace)},
    )
    environment = SemanticEnvironment(
        MappingProxyType({}), MappingProxyType({}), MappingProxyType({}),
        MappingProxyType({}), callables, extension_registry=registry,
    )
    return environment


@pytest.mark.parametrize('reverse', [False, True])
def test_ineligible_union_candidate_does_not_abort_valid_rotation_overload(tmp_path, reverse):
    """The type of one candidate's tuple cannot veto another valid candidate."""
    env = _pick_registry(tmp_path, reverse=reverse)
    call = ast.parse('pick((1, 2, 3), 1.5)', mode='eval').body
    selected = analyze_expression(call, env).facts[call].analyzed_call
    assert selected.extension_overload_index == (0 if reverse else 1)
    assert selected.result.typ is NFType.ROTATION
    assert selected.static_operands[0].value == (1.0, 2.0, 3.0)


def test_locally_ambiguous_union_does_not_shadow_compatible_rotation_candidate(tmp_path):
    """An undecidable union stays ineligible even when another candidate uses Int -> Float."""
    env = _pick_registry(tmp_path)
    call = ast.parse('pick((1, 2, 3), 1)', mode='eval').body
    selected = analyze_expression(call, env).facts[call].analyzed_call
    assert selected.extension_overload_index == 1
    assert selected.result.typ is NFType.ROTATION


def test_direct_union_candidate_reports_ambiguity_when_other_candidates_are_ineligible(tmp_path):
    """An otherwise-eligible Rotation|Vector union cannot infer a unique tuple type."""
    env = _pick_registry(tmp_path)
    call = ast.parse('pick((1, 2, 3), True)', mode='eval').body
    with pytest.raises(CompileError, match='do not match'):
        analyze_expression(call, env)


def test_union_candidate_remains_ambiguous_when_alone(tmp_path):
    """A sole otherwise-matching Rotation|Vector union reports its true ambiguity."""
    env = _pick_registry(tmp_path, only_union=True)
    call = ast.parse('pick((1, 2, 3), 1)', mode='eval').body
    with pytest.raises(CompileError, match='Ambiguous.*Vector and Rotation'):
        analyze_expression(call, env)


def test_candidate_matching_is_independent_of_source_keyword_order(tmp_path):
    """The source argument order does not control which overload is eligible."""
    env = _pick_registry(tmp_path)
    for expression in ('pick(value=(1, 2, 3), mode=1.5)',
                       'pick(mode=1.5, value=(1, 2, 3))'):
        call = ast.parse(expression, mode='eval').body
        assert analyze_expression(call, env).facts[call].analyzed_call.extension_overload_index == 1
