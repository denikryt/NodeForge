"""Local analysis preserves distinct capture and callee filtering contracts."""

import ast
from types import SimpleNamespace

import pytest

from NodeForge.semantic import source_callables
from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType


def function(source):
    return ast.parse(source).body[0]


@pytest.mark.parametrize('source,free_names,callees,bindings', [
    ('def f():\n return h(x, h(y), key=g(z))', ('x', 'y', 'z'), ('h', 'g'), set()),
    ('def f(h, x):\n return h(x, extra)', ('extra',), ('h',), {'h', 'x'}),
    ('def f(x):\n h=1\n return h(x)', (), ('h',), {'h', 'x'}),
    ('def f():\n for h in items:\n  h(x)', ('items', 'x'), ('h',), {'h'}),
    ('def f():\n return m.h(g(x))', ('x',), ('g',), set()),
    ('def f(m):\n return m.h(g(x))', ('x',), ('g',), {'m'}),
    ('def f():\n return factory()(g(x))', ('x',), ('factory', 'g'), set()),
    ('def f():\n return obj[h(x)](g(y))', ('obj', 'x', 'y'), ('h', 'g'), set()),
    ('def f():\n return h(*items, **options)', ('items', 'options'), ('h',), set()),
    ('def f():\n def nested():\n  hidden=1\n  return ignored(x)\n return h(y)',
     ('y',), ('h',), {'nested', 'hidden'}),
])
def test_local_analysis_preserves_order_and_lexical_facts(source, free_names, callees, bindings):
    analysis = source_callables._analyze_local_names(function(source), {'m': object()})
    assert analysis.free_names == free_names
    assert analysis.callee_names == callees
    assert analysis.local_bindings == frozenset(bindings)


@pytest.mark.parametrize('local_statement', ['h=1', 'for h in items:\n  pass'])
def test_transitive_callee_filter_keeps_lexically_bound_names(local_statement):
    fn = function('def f():\n ' + local_statement.replace('\n', '\n ') + '\n return h(x)')
    assert source_callables.called_local_functions(fn, {'h': object()}) == ('h',)
    analysis = source_callables._analyze_local_names(fn)
    assert tuple(name for name in analysis.callee_names if name not in analysis.local_bindings) == ()


def test_capture_analysis_reuses_facts_and_keeps_bound_transitive_callee(monkeypatch):
    outer = function('def outer(helper, x):\n return helper(x)')
    inner = function('def helper(x):\n return x+bias')
    functions = {'outer': outer, 'helper': inner}
    original = source_callables._binding_names_in_local_function
    calls = []

    def bindings(fn):
        calls.append(fn.name)
        return original(fn)

    monkeypatch.setattr(source_callables, '_binding_names_in_local_function', bindings)
    captures = source_callables.analyze_local_captures(
        outer, local_functions=functions,
        runtime_bindings={'bias': SimpleNamespace(typ=NFType.FLOAT)},
        compile_time_values={}, reserved_name_labels={},
        callable_environment=CallableEnvironment(frozenset(), functions, {}),
    )
    assert [capture.name for capture in captures] == ['bias']
    assert calls == ['outer', 'helper']


def test_bound_transitive_callee_still_rejects_unforwardable_capture():
    outer = function('def outer(x):\n helper=1\n bias=2\n return helper(x)')
    inner = function('def helper(x):\n return x+bias')
    with pytest.raises(CompileError, match='cannot forward capture bias'):
        source_callables.analyze_local_captures(
            outer, local_functions={'helper': inner},
            runtime_bindings={'bias': SimpleNamespace(typ=NFType.FLOAT)},
            compile_time_values={}, reserved_name_labels={},
        )
