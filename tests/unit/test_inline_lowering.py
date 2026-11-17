"""Backend contract tests for occurrence-scoped source IR without Blender RNA."""

from __future__ import annotations

import pytest

from NodeForge.blender import ir_lowering as backend
from NodeForge.blender.values import Value
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.tests.unit.test_inline_source_calls import _analyze


class StubSocket:
    """Stand in for a typed Blender output socket during detached lowering tests."""


def _emit_stub_value(calls, name, typ):
    """Return one materialization stub and record the expected native operation."""
    def emit(group, *args, **kwargs):
        calls.append((name, args))
        return Value(StubSocket(), typ)
    return emit


def _lower_inline(source, monkeypatch, runtime_values=()):
    """Run the real prepared IR through backend dispatch with stub native nodes."""
    operations, session = _analyze(source)
    assert len(operations) == 1
    operation = operations[0]
    calls = []
    monkeypatch.setattr(backend, '_value', _emit_stub_value(calls, 'Float', NFType.FLOAT))
    monkeypatch.setattr(backend, '_int_value', _emit_stub_value(calls, 'Int', NFType.INT))
    monkeypatch.setattr(backend, '_bool_value', _emit_stub_value(calls, 'Bool', NFType.BOOL))
    monkeypatch.setattr(backend, '_string_value', _emit_stub_value(calls, 'String', NFType.STRING))
    monkeypatch.setattr(backend, '_vector_value', _emit_stub_value(calls, 'Vector', NFType.VECTOR))
    monkeypatch.setattr(backend, '_color_value', _emit_stub_value(calls, 'Color', NFType.COLOR))
    monkeypatch.setattr(backend, '_math', _emit_stub_value(calls, 'Math', NFType.FLOAT))
    context = backend.BlenderIRLoweringContext(
        group=object(), runtime_bindings={}, source_callable_session=session,
        function_materializer=object(), function_materialization_context=object(),
    )
    result = backend._lower_source_call(context, operation, runtime_values, 0, 0)
    return operation, calls, result


def test_bound_inline_uses_no_group_and_realizes_defaults(monkeypatch):
    """The prepared source body consumes its typed positional and default values."""
    _, calls, result = _lower_inline('''def calc(value: Float, offset: Float = 4.0, __inline__=True) -> Float:
    return value + offset
x = calc(2.0)
''', monkeypatch)
    assert len(result) == 1 and result[0].typ is NFType.FLOAT
    assert [name for name, _ in calls] == ['Float', 'Float', 'Math']
    assert [args[0] for name, args in calls if name == 'Float'] == [2.0, 4.0]


def test_bound_inline_preserves_panel_reordered_input_identity(monkeypatch):
    """A reordered panel does not change the positional binding declaration."""
    operation, calls, result = _lower_inline('''def calc(a: Float, b: Float, c: Float = 1, __inline__=True) -> Float:
    panel([c, a], name="Scalars")
    return a + b
x = calc(2.0, 3.0)
''', monkeypatch)
    assert [arg.parameter_index for arg in operation.static_arguments] == [2, 0]
    assert result[0].typ is NFType.FLOAT
    assert [args[0] for name, args in calls if name == 'Float'] == [2.0, 3.0, 1.0]


def test_bound_inline_constant_adapters_cover_scalar_vector_and_rgba(monkeypatch):
    """All permitted static/default types produce typed runtime values."""
    cases = [
        ('Int', '3', 'Int', NFType.INT),
        ('Bool', 'True', 'Bool', NFType.BOOL),
        ('String', '"ok"', 'String', NFType.STRING),
        ('Vector', 'vector(1,2,3)', 'Vector', NFType.VECTOR),
        ('Color', '(0.1,0.2,0.3,0.4)', 'Color', NFType.COLOR),
    ]
    for typ, expr, expected, nf_type in cases:
        _op, calls, result = _lower_inline(
            f'def identity(value: {typ} = {expr}, __inline__=True) -> {typ}:\n'
            '    return value\n'
            'x = identity()\n', monkeypatch,
        )
        assert [name for name, _ in calls] == [expected]
        assert result[0].typ is nf_type


def test_bound_inline_coerces_runtime_int_to_float(monkeypatch):
    """A caller Int field is converted to a typed Float socket before binding."""
    _, calls, result = _lower_inline('''def calc(value: Float, __inline__=True) -> Float:
    return value
input_value = index()
x = calc(input_value)
''', monkeypatch, (Value(StubSocket(), NFType.INT),))
    assert [name for name, _ in calls] == ['Float', 'Math']
    assert result[0].typ is NFType.FLOAT


def test_bound_inline_rejects_bad_backend_input_before_body_execution(monkeypatch):
    """An incompatible runtime Value cannot cross the prepared call boundary."""
    with pytest.raises(CompileError, match='inline argument expects'):
        _lower_inline('''def calc(value: Float, __inline__=True) -> Float:
    return value
x = calc(index())
''', monkeypatch, (Value(StubSocket(), NFType.VECTOR),))


def test_lower_body_requires_explicit_bound_mode_contract(monkeypatch):
    """BOUND cannot accidentally request Blender group interfaces or omit its map."""
    operation, session = _analyze('''def calc(value: Float, __inline__=True) -> Float:
    return value
x = calc(3)
''')
    prepared = session.get_inline_prepared(operation[0].materialization)
    context = backend.BlenderIRLoweringContext(object(), {})
    with pytest.raises(ValueError, match='bound body requires'):
        backend.lower_body(context, prepared.group.body, {}, input_mode='BOUND')
    with pytest.raises(CompileError, match='prebound inline input'):
        backend.lower_body(context, prepared.group.body, {}, input_mode='BOUND', bound_inputs={})
