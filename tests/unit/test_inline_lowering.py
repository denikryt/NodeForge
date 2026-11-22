"""Backend contract tests for occurrence-scoped source IR without Blender RNA."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from NodeForge.blender import ir_lowering as backend
from NodeForge.blender.values import Value, DeferredConstant
from NodeForge.errors import CompileError
from NodeForge.semantic.ir import IRInputDeclaration
from NodeForge.nf_types import NFType
from NodeForge.tests.unit.test_inline_source_calls import _analyze


class StubSocket:
    """Stand in for a typed Blender output socket during detached lowering tests."""


@pytest.fixture(autouse=True)
def _host_only_physical_output_stub(monkeypatch):
    """Real Blender validates RNA owners; unit-only bare groups use producer stubs."""
    original = backend.require_output

    def require(group, operand, *, context="constant", allow_native_conversion=False):
        if type(group) is object:
            if isinstance(operand, Value):
                return operand
            if isinstance(operand, DeferredConstant):
                emit = {
                    NFType.FLOAT: backend._value,
                    NFType.INT: backend._int_value,
                    NFType.BOOL: backend._bool_value,
                    NFType.STRING: backend._string_value,
                    NFType.VECTOR: backend._vector_value,
                    NFType.COLOR: backend._color_value,
                    NFType.ROTATION: backend._rotation_value,
                }[operand.typ]
                return emit(group, operand.value)
        return original(group, operand, context=context,
                        allow_native_conversion=allow_native_conversion)

    monkeypatch.setattr(backend, 'require_output', require)


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
    monkeypatch.setattr(backend, '_rotation_value', _emit_stub_value(calls, 'Rotation', NFType.ROTATION))
    monkeypatch.setattr(backend, '_math', _emit_stub_value(calls, 'Math', NFType.FLOAT))
    from NodeForge.blender.values import DeferredConstant

    def fake_required_output(group, operand, *, context="constant", allow_native_conversion=False):
        """Host-only stubs have no Blender RNA; use their typed fake producers."""
        if isinstance(operand, Value):
            return operand
        assert isinstance(operand, DeferredConstant)
        producer = {
            NFType.FLOAT: backend._value,
            NFType.INT: backend._int_value,
            NFType.BOOL: backend._bool_value,
            NFType.STRING: backend._string_value,
            NFType.VECTOR: backend._vector_value,
            NFType.COLOR: backend._color_value,
            NFType.ROTATION: backend._rotation_value,
        }[operand.typ]
        return producer(group, operand.value)

    monkeypatch.setattr(backend, 'require_output', fake_required_output)
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
    assert [name for name, _ in calls] == ['Math']  # both literal operands use Math socket defaults


def test_bound_inline_preserves_panel_reordered_input_identity(monkeypatch):
    """A reordered panel does not change the positional binding declaration."""
    operation, calls, result = _lower_inline('''def calc(a: Float, b: Float, c: Float = 1, __inline__=True) -> Float:
    panel([c, a], name="Scalars")
    return a + b
x = calc(2.0, 3.0)
''', monkeypatch)
    assert [arg.parameter_index for arg in operation.static_arguments] == [2, 0]
    assert result[0].typ is NFType.FLOAT
    assert not [name for name, args in calls if name == 'Float']  # unused c and direct a/b create no Value producers


def test_bound_inline_constant_adapters_cover_scalar_vector_and_rgba(monkeypatch):
    """All permitted static/default types produce typed runtime values."""
    cases = [
        ('Int', '3', 'Int', NFType.INT),
        ('Bool', 'True', 'Bool', NFType.BOOL),
        ('String', '"ok"', 'String', NFType.STRING),
        ('Vector', 'vector(1,2,3)', 'Vector', NFType.VECTOR),
        ('Color', '(0.1,0.2,0.3,0.4)', 'Color', NFType.COLOR),
        ('Rotation', '(0.1,-0.2,7.0)', 'Rotation', NFType.ROTATION),
    ]
    for typ, expr, expected, nf_type in cases:
        _op, calls, result = _lower_inline(
            f'def identity(value: {typ} = {expr}, __inline__=True) -> {typ}:\n'
            '    return value\n'
            'x = identity()\n', monkeypatch,
        )
        assert [name for name, _ in calls] == [expected]
        assert result[0].typ is nf_type


def test_inline_rotation_default_materializes_once(monkeypatch):
    """An omitted Rotation default binds one canonical typed runtime value."""
    _, calls, result = _lower_inline('''def echo(rotation: Rotation = (0.1, -0.2, 7.0), __inline__=True) -> Rotation:
    return rotation
result = echo()
''', monkeypatch)
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    assert result[0].typ is NFType.ROTATION
    assert [name for name, _ in calls] == ['Rotation']
    (angles,) = calls[0][1]
    assert angles == tuple(normalize_float_constant(v) for v in (0.1, -0.2, 7.0))


def test_inline_rotation_static_materializer_uses_same_canonical_contract(monkeypatch):
    """An already-bound static Rotation value uses the same inline adapter."""
    calls = []
    monkeypatch.setattr(backend, '_rotation_value', _emit_stub_value(calls, 'Rotation', NFType.ROTATION))
    result = backend._inline_constant_value(
        SimpleNamespace(group=object()), NFType.ROTATION, [0.1, -0.2, 7.0], 10, 20,
    )
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    assert result.typ is NFType.ROTATION
    assert calls == [('Rotation', (tuple(normalize_float_constant(v) for v in (0.1, -0.2, 7.0)), 10, 20))]


def test_bound_inline_coerces_runtime_int_to_float(monkeypatch):
    """A caller Int field is converted to a typed Float socket before binding."""
    _, calls, result = _lower_inline('''def calc(value: Float, __inline__=True) -> Float:
    return value
input_value = index()
x = calc(input_value)
''', monkeypatch, (Value(StubSocket(), NFType.INT),))
    assert [name for name, _ in calls] == ['Math']
    assert result[0].typ is NFType.FLOAT


def test_bound_inline_rejects_bad_backend_input_before_body_execution(monkeypatch):
    """An incompatible runtime Value cannot cross the prepared call boundary."""
    with pytest.raises(CompileError, match='inline argument expects'):
        _lower_inline('''def calc(value: Float, __inline__=True) -> Float:
    return value
x = calc(index())
''', monkeypatch, (Value(StubSocket(), NFType.VECTOR),))


def test_lower_body_requires_prebound_typed_runtime_input():
    """Inline declarations must already have a matching typed BindingId value."""
    operation, session = _analyze('''def calc(value: Float, __inline__=True) -> Float:
    return value
x = calc(3)
''')
    prepared = session.get_inline_prepared(operation[0].materialization)
    context = backend.BlenderIRLoweringContext(object(), {})
    declaration, = [item for item in prepared.group.body.statements
                    if isinstance(item, IRInputDeclaration)]
    with pytest.raises(CompileError, match='prebound inline input'):
        backend.lower_body(context, prepared.group.body, {})
    with pytest.raises(CompileError, match='prebound inline input'):
        backend.lower_body(context, prepared.group.body, {
            declaration.target_binding_id: Value(StubSocket(), NFType.INT),
        })
    result = backend.lower_body(context, prepared.group.body, {
        declaration.target_binding_id: Value(StubSocket(), NFType.FLOAT),
    }, physical_outputs=False)
    assert result.explicit_outputs[0][1].typ is NFType.FLOAT


def test_inline_object_wrappers_do_not_inherit_previous_info_cache():
    """An Object source socket is borrowed, but physical info caches are invocation-local."""
    from NodeForge.blender.values import ObjectValue

    original = ObjectValue(StubSocket())
    original._object_info_outputs = {'location': Value(StubSocket(), NFType.VECTOR)}
    original._object_info_cache_config = ('ORIGINAL', True)
    copied = backend._inline_runtime_value(SimpleNamespace(group=object()), NFType.OBJECT, original, 0, 0)
    assert isinstance(copied, ObjectValue)
    assert copied is not original and copied.socket is original.socket
    assert copied._object_info_outputs is None
    assert copied._object_info_cache_config is None


def test_two_sibling_occurrences_receive_distinct_layout_lanes(monkeypatch):
    """Repeated calls lower into the same group with distinct physical origins."""
    source = '''def add(value: Float, __inline__=True) -> Float:
    return value + 1.0
x = add(2.0)
y = add(3.0)
'''
    calls, session = _analyze(source)
    native = []
    monkeypatch.setattr(backend, '_value', _emit_stub_value(native, 'Float', NFType.FLOAT))
    monkeypatch.setattr(backend, '_math', _emit_stub_value(native, 'Math', NFType.FLOAT))
    context = backend.BlenderIRLoweringContext(
        group=object(), runtime_bindings={}, source_callable_session=session,
        function_materializer=object(), function_materialization_context=object(),
    )
    first = backend._lower_source_call(context, calls[0], (), 0, 0)
    second = backend._lower_source_call(context, calls[1], (), 0, 0)
    assert first[0].socket is not second[0].socket
    math_rows = [args for name, args in native if name == 'Math']
    assert len(math_rows) == 2
    assert math_rows[0][-1] != math_rows[1][-1]
    assert math_rows[0][-1] - math_rows[1][-1] == 640


def test_inline_context_map_cannot_mutate_caller(monkeypatch):
    """Local context writes never replace the caller's context slot or wrapper."""
    from NodeForge.semantic.group_context import GroupContextSlot

    calls, session = _analyze('''def identity(value: Float, __inline__=True) -> Float:
    return value
x = identity(2.0)
''')
    monkeypatch.setattr(backend, '_value', _emit_stub_value([], 'Float', NFType.FLOAT))
    outer = Value(StubSocket(), NFType.VECTOR)
    context = backend.BlenderIRLoweringContext(
        group=object(), runtime_bindings={}, group_context_values={GroupContextSlot.GRID_UV: outer},
        source_callable_session=session, function_materializer=object(), function_materialization_context=object(),
    )
    original_lower = backend.lower_body
    def captured_lower(child, *args, **kwargs):
        assert child.group_context_values is not context.group_context_values
        assert child.group_context_values[GroupContextSlot.GRID_UV] is not outer
        child.group_context_values[GroupContextSlot.GRID_UV] = Value(StubSocket(), NFType.VECTOR)
        return original_lower(child, *args, **kwargs)
    monkeypatch.setattr(backend, 'lower_body', captured_lower)
    backend._lower_source_call(context, calls[0], (), 0, 0)
    assert context.group_context_values[GroupContextSlot.GRID_UV] is outer


def test_inline_lowering_records_dependency_on_active_physical_frame(monkeypatch):
    """Only the current materialized parent traces its inlined semantic body."""
    from NodeForge.function_instances import FunctionCompilationTrace
    from NodeForge.compiler_identities import inline_expansion_owner_scope

    calls, session = _analyze('''def leaf(value: Float, __inline__=True) -> Float:
    return value + 1.0
result = leaf(2.0)
''')
    monkeypatch.setattr(backend, '_value', _emit_stub_value([], 'Float', NFType.FLOAT))
    monkeypatch.setattr(backend, '_math', _emit_stub_value([], 'Math', NFType.FLOAT))
    trace = FunctionCompilationTrace()
    context = backend.BlenderIRLoweringContext(
        group=object(), runtime_bindings={}, source_callable_session=session,
        function_materializer=object(),
        function_materialization_context=SimpleNamespace(function_compilation_trace=trace),
    )
    with trace.group('PARENT', {'source': 'unchanged'}) as frame:
        assert backend._lower_source_call(context, calls[0], (), 0, 0)
        assert len(frame.child_rows) == 1
        assert frame.child_rows[0]['owner'] == inline_expansion_owner_scope(
            calls[0].materialization.call_site)
        assert frame.finish('interface').fingerprint
    assert trace.current is None


def test_failed_inline_lowering_does_not_report_completed_dependency(monkeypatch):
    """A failed expansion cannot be fingerprinted as successfully realized."""
    from NodeForge.function_instances import FunctionCompilationTrace

    calls, session = _analyze('''def leaf(value: Float, __inline__=True) -> Float:
    return value
result = leaf(index())
''')
    trace = FunctionCompilationTrace()
    context = backend.BlenderIRLoweringContext(
        group=object(), runtime_bindings={}, source_callable_session=session,
        function_materializer=object(),
        function_materialization_context=SimpleNamespace(function_compilation_trace=trace),
    )
    with trace.group('PARENT', {}) as frame:
        with pytest.raises(CompileError, match='inline argument expects'):
            backend._lower_source_call(context, calls[0], (Value(StubSocket(), NFType.VECTOR),), 0, 0)
        assert frame.child_rows == []
