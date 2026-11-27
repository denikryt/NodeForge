"""Declarative structured parameters preserve leaf acquisition and typed IR."""
import ast
from dataclasses import replace
from types import MappingProxyType

import pytest

from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.semantic.analysis import analyze_expression
from NodeForge.semantic.compile_time import ConstVector, detached_compile_time_mapping
from NodeForge.semantic.ir import IRCall
from NodeForge.semantic.lowering import lower_analyzed_expression
from test_extension_dynamic_results import _owner, _dynamic_semantic_environment

pytestmark = pytest.mark.unit

INTERFACE = '''from typing import Annotated
from NodeForge import Float, Int, String, Vector, Rotation, EvaluationMode, DynamicResults
EXTENSION_API=2
EXTENSIONS={'foo': '.backend:foo'}
def foo(items: Annotated[dict[str, Float | Int | Vector | Rotation], EvaluationMode.COMPILE_TIME_OR_RUNTIME] = {'default': 1.0},
        *, labels: Annotated[list[String], EvaluationMode.COMPILE_TIME_ONLY] = ['a', 'b']) -> DynamicResults: ...
'''
SEMANTIC = '''from NodeForge.nf_types import NFType
calls = []
def _result_types_foo(args):
    calls.append((tuple(args['items'].items()), tuple(args['labels'])))
    args['items'].clear()
    args['labels'].clear()
    return NFType.FLOAT
'''


def _environment(tmp_path, interface=INTERFACE, semantic=SEMANTIC):
    """Load a synthetic owner and expose its frontend environment."""
    owner = _owner(tmp_path, interface, semantic=semantic, backend='def foo(*args, **kwargs): pass\n')
    env, _ = _dynamic_semantic_environment(owner)
    return owner, env


def _call(env, expression):
    """Lower a call through the real analysis and flat IR path."""
    root = ast.parse(expression, mode='eval').body
    analysis = analyze_expression(root, env)
    return next(op for op in lower_analyzed_expression(root, analysis).operations if isinstance(op, IRCall) and op.target.extension_callable_id is not None)


def test_exact_layout_static_types_and_mutation_isolation(tmp_path):
    owner, env = _environment(tmp_path)
    env = replace(env, const_eval_values=MappingProxyType({'key': 'a,b', 'options': ['L', 'H']}))
    call = _call(env, "foo({key: f, 'weight': 0.5, 'i': i, 'v': vector(1.,2.,3.)}, labels=options)")
    assert call.structured_arguments == ((0, ('a,b','weight','i','v')), (1,(0,1)))
    assert [(a.variadic_index,a.value.typ) for a in call.arguments] == [(0,NFType.FLOAT),(2,NFType.INT)]
    assert [(a.parameter_index,a.variadic_index,a.static_type,a.value) for a in call.static_arguments] == [
        (0,1,NFType.FLOAT,0.5),(0,3,NFType.VECTOR,(1.,2.,3.)),(1,0,NFType.STRING,'L'),(1,1,NFType.STRING,'H')]
    first = _call(env, 'foo()')
    second = _call(env, 'foo()')
    assert first.structured_arguments == second.structured_arguments == ((0,('default',)),(1,(0,1)))
    assert first.static_arguments == second.static_arguments
    assert _call(env, 'foo({}, labels=[])').structured_arguments == ((0,()),(1,()))
    assert len(owner._phase_modules['SEMANTIC']['semantic'].calls) == 4


@pytest.mark.parametrize('source', ["foo({'a': 1, 'a': 2})", "foo({f: 1})", "foo({1: 1})", "foo({'': 1})", "foo({'a': None})", "foo({'a': (1.,2.,3.)})", "foo({'a': 'bad'})", "foo([1])", "foo({**{}})", "foo({}, labels=('a',))", "foo({}, labels=[s])", "foo({}, labels=[*['a']])"])
def test_invalid_structures_fail_before_resolver(tmp_path, source):
    owner, env = _environment(tmp_path)
    with pytest.raises(CompileError):
        _call(env, source)
    assert owner._phase_modules['SEMANTIC']['semantic'].calls == []


def test_typed_vector_detachment_and_bound_list_member(tmp_path):
    vector = ConstVector((1.,2.,3.))
    state = detached_compile_time_mapping({'v0': vector, 'items': [vector]})
    assert type(state['v0']) is ConstVector and state['v0'] is state['items'][0]
    _, env = _environment(tmp_path)
    env = replace(env, const_eval_values=MappingProxyType(state))
    call = _call(env, "foo({'one': v0, 'two': items[0]})")
    assert [a.static_type for a in call.static_arguments[:2]] == [NFType.VECTOR]*2


@pytest.mark.parametrize('layout', [((0,('a','a')), (1,(0,1))), ((0,('a','b')), (1,(0,1))), ((1,(0,1)), (0,('a',))), ((0,('a',)), (1,(0,2)))])
def test_malformed_ir_layout_rejected(tmp_path, layout):
    _, env = _environment(tmp_path)
    call = _call(env, "foo({'a': f})")
    with pytest.raises(ValueError):
        replace(call, structured_arguments=layout)


@pytest.mark.parametrize('declaration', [
    "def foo(*items: Annotated[list[Float], EvaluationMode.RUNTIME_ONLY]) -> Float: ...",
    "def foo(items: Annotated[list[Float], EvaluationMode.RUNTIME_ONLY] = []) -> Float: ...",
    "def foo(items: Annotated[dict[str, Float], EvaluationMode.COMPILE_TIME_ONLY] = None) -> Float: ...",
    "def foo(items: Annotated[dict[str, Float], EvaluationMode.COMPILE_TIME_ONLY] = {'': 1.0}) -> Float: ...",
    "def foo(items: Annotated[list[Float], EvaluationMode.COMPILE_TIME_ONLY] = [None]) -> Float: ...",
    "def foo(items: Annotated[list[Float], EvaluationMode.RUNTIME_ONLY], /) -> Float: ...",
])
def test_invalid_parameter_declarations(tmp_path, declaration):
    interface = INTERFACE[:INTERFACE.index('def foo')] + declaration
    owner = _owner(tmp_path, interface, backend='def foo(*args, **kwargs): pass\n')
    with pytest.raises(CompileError):
        owner.normalize_interface()


def test_semantic_body_rejected_before_leaf_acquisition(tmp_path):
    owner, env = _environment(tmp_path, semantic=SEMANTIC + '\nfrom NodeForge import Float\ndef foo(items, *, labels) -> Float:\n    raise AssertionError("unexpected semantic body")\n')
    with pytest.raises(CompileError, match='BACKEND_ONLY'):
        _call(env, "foo({'x': unknown_name})")
    assert owner._phase_modules['SEMANTIC']['semantic'].calls == []


def test_exact_unicode_keys_and_ct_error_not_promoted(tmp_path):
    owner, env = _environment(tmp_path)
    key = '界'*21
    assert _call(env, f'foo({{{key!r}: f}})').structured_arguments[0] == (0,(key,))
    with pytest.raises(CompileError, match='63-byte'):
        _call(env, f'foo({{{key+"x"!r}: f}})')
    env = replace(env, const_eval_values=MappingProxyType({'a': [1.0]}))
    with pytest.raises(CompileError, match='indexing failed'):
        _call(env, "foo({'bad': a[10]})")


def test_wrong_selected_static_metadata_is_rejected(tmp_path):
    _, env = _environment(tmp_path)
    call = _call(env, "foo({'weight': 1.0})")
    with pytest.raises(ValueError, match='selected NFType'):
        replace(call, static_arguments=(replace(call.static_arguments[0],static_type=None),*call.static_arguments[1:]))
