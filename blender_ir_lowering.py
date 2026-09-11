"""Materialize value-based NodeForge Semantic IR into Blender Geometry Nodes."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .constants import TYPE_BOOL, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_VECTOR
from .errors import CompileError
from .compiler_identities import BindingId
from .group_context import GroupContextSlot
from .nodes import _boolean_math, _combine_xyz_mixed, _compare, _int_value, _math, _separate_xyz, _string_value, _switch, _value, _vector_math
from .geometry import (
    _capture_attribute_geometry,
    _cube_geometry,
    _empty_geometry,
    _instance_on_points,
    _join_geometry,
    _line_geometry,
    _grid_geometry,
    _point_geometry,
    _points_geometry,
    _polyline_geometry,
    _realize_instances,
    _set_material_geometry,
    _set_position_geometry,
    _store_named_attribute_geometry,
    _transform_geometry,
)
from .nodes import _id as _field_id, _index as _field_index, _normal as _field_normal, _position as _field_position
from .builtins.bundle import build_bundle, build_bundle_get, build_bundle_set
from .builtins.object_info import resolve_object_property_explicit
from .builtins.raw_nodes import build_materialized_raw_node
from .semantic_ir import (
    IRBinary,
    IRCall,
    IRCallableKind,
    IRNamedOutputs,
    IRTuple,
    IRBinding,
    IRBoolBinary,
    IRCompare,
    IRArray,
    IRConditional,
    IRContextRead,
    IRContextWrite,
    IRLiteral,
    IRObjectProperty,
    IRUnary,
    IRVectorComponent,
    IRVectorLiteral,
    IRAssign,
    IRBody,
    IRFinalExpression,
    IRInputDeclaration,
    IROutput,
    IRBindLeaves,
    IRDiscardExpression,
    IRIf,
    IRRepeat,
    IRPanelDeclaration,
)
from .values import NodeResult, ObjectValue, TupleValue, Value
from .interface import _create_group_input_socket, _create_interface_panel, interface_item_for_group_input_value
from .runtime import _create_repeat_zone, _socket_by_name


@dataclass(frozen=True)
class BlenderIRLoweringContext:
    """Explicit Blender backend inputs for one Semantic IR lowering operation.

    Source semantic analysis must not receive this context. It contains only the
    target Geometry Nodes group and already-materialized runtime bindings needed
    to realize a validated Semantic IR program in Blender.
    """

    group: object
    runtime_bindings: Mapping[BindingId, Value]
    group_context_values: Mapping[GroupContextSlot, Value] = None

    def __post_init__(self):
        """Freeze runtime bindings and detach mutable contextual backend state."""
        object.__setattr__(self, "runtime_bindings", MappingProxyType(dict(self.runtime_bindings)))
        initial = {} if self.group_context_values is None else dict(self.group_context_values)
        if not all(isinstance(slot, GroupContextSlot) and isinstance(value, Value) for slot, value in initial.items()):
            raise TypeError("group_context_values must map GroupContextSlot to Value")
        object.__setattr__(self, "group_context_values", initial)


def _position(depth):
    """Return the existing expression-layout coordinates for *depth*."""
    return depth * 240, -depth * 90


def _materialized_value(materialized, value):
    """Return one already-materialized operand or raise a controlled invariant error."""
    try:
        return materialized[value.id]
    except KeyError as exc:
        raise CompileError(
            f"Internal error: Semantic IR value v{value.id} was used before materialization"
        ) from exc


def _store_result(materialized, result, value):
    """Validate and register one backend value for an IR result."""
    if not isinstance(value, Value):
        raise CompileError(
            f"Internal error: Semantic IR value v{result.id} materialized as {type(value).__name__}, not Value"
        )
    if value.typ != result.typ:
        raise CompileError(
            f"Internal error: Semantic IR value v{result.id} expected type {result.typ}, got {value.typ}"
        )
    materialized[result.id] = value


def _lower_literal(context, operation, materialized, x, y):
    """Materialize one literal operation with the existing Blender node topology."""
    if operation.result.typ == TYPE_BOOL:
        val = _value(context.group, 1.0 if operation.value else 0.0, x, y)
        zero = _value(context.group, 0.0, x + 20, y - 40)
        result = _compare(context.group, "NOT_EQUAL", val, zero, x, y)
    elif operation.result.typ == TYPE_FLOAT:
        result = _value(context.group, operation.value, x, y)
    elif operation.result.typ == TYPE_INT:
        result = _int_value(context.group, operation.value, x, y)
    else:
        result = _string_value(context.group, operation.value, x, y)
    _store_result(materialized, operation.result, result)


def _lower_binding(context, operation, materialized, runtime_bindings):
    """Resolve one semantic runtime binding to its backend materialization."""
    value = runtime_bindings.get(operation.binding_id)
    if not isinstance(value, Value):
        raise CompileError(
            f"Internal error: Semantic IR binding {operation.binding_id!r} is no longer a runtime Value"
        )
    if value.typ != operation.result.typ:
        raise CompileError(
            f"Internal error: Semantic IR binding {operation.binding_id!r} changed type "
            f"from {operation.result.typ} to {value.typ}"
        )
    _store_result(materialized, operation.result, value)


def _lower_unary(context, operation, materialized, x, y):
    """Materialize one typed unary IR operation."""
    operand = _materialized_value(materialized, operation.operand)
    if operation.op == "-":
        if operation.operand.typ in {TYPE_FLOAT, TYPE_INT}:
            zero = _value(context.group, 0.0, x, y - 40)
            result = _math(context.group, "SUBTRACT", [zero, operand], x, y)
        elif operation.operand.typ == TYPE_VECTOR:
            minus_one = _value(context.group, -1.0, x, y - 40)
            result = _vector_math(context.group, "SCALE", [operand, minus_one], TYPE_VECTOR, x, y)
        else:
            raise CompileError(
                f"Internal error: unsupported Semantic IR unary '-' operand {operation.operand.typ}"
            )
    elif operation.op == "not":
        result = _boolean_math(context.group, "NOT", [operand], x, y)
    else:
        raise CompileError(f"Internal error: unsupported Semantic IR unary operation {operation.op!r}")
    _store_result(materialized, operation.result, result)


def _lower_binary(context, operation, materialized, x, y):
    """Materialize one typed binary IR operation."""
    left = _materialized_value(materialized, operation.left)
    right = _materialized_value(materialized, operation.right)
    if operation.left.typ in {TYPE_FLOAT, TYPE_INT} and operation.right.typ in {TYPE_FLOAT, TYPE_INT}:
        result = _math(context.group, operation.op, [left, right], x, y)
    elif operation.op in {"ADD", "SUBTRACT"} and operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_VECTOR:
        result = _vector_math(context.group, operation.op, [left, right], TYPE_VECTOR, x, y)
    elif operation.op == "MULTIPLY":
        if operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_FLOAT:
            result = _vector_math(context.group, "SCALE", [left, right], TYPE_VECTOR, x, y)
        elif operation.left.typ == TYPE_FLOAT and operation.right.typ == TYPE_VECTOR:
            result = _vector_math(context.group, "SCALE", [right, left], TYPE_VECTOR, x, y)
        elif operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_VECTOR:
            result = _vector_math(context.group, "MULTIPLY", [left, right], TYPE_VECTOR, x, y)
        else:
            raise CompileError(
                f"Internal error: unsupported Semantic IR binary lowering for "
                f"{operation.left.typ} {operation.op} {operation.right.typ}"
            )
    elif operation.op == "DIVIDE" and operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_FLOAT:
        inv = _math(context.group, "DIVIDE", [_value(context.group, 1.0, x, y - 40), right], x, y)
        result = _vector_math(context.group, "SCALE", [left, inv], TYPE_VECTOR, x, y)
    else:
        raise CompileError(
            f"Internal error: unsupported Semantic IR binary lowering for "
            f"{operation.left.typ} {operation.op} {operation.right.typ}"
        )
    _store_result(materialized, operation.result, result)


def _lower_bool_binary(context, operation, materialized, x, y):
    """Materialize one pairwise Boolean IR operation."""
    left = _materialized_value(materialized, operation.left)
    right = _materialized_value(materialized, operation.right)
    result = _boolean_math(context.group, operation.op, [left, right], x, y)
    _store_result(materialized, operation.result, result)


def _lower_compare(context, operation, materialized, x, y):
    """Materialize one typed comparison IR operation."""
    left = _materialized_value(materialized, operation.left)
    right = _materialized_value(materialized, operation.right)
    result = _compare(context.group, operation.op, left, right, x, y)
    _store_result(materialized, operation.result, result)


def _lower_conditional(context, operation, materialized, x, y):
    """Materialize one conditional IR operation with legacy socket ordering."""
    condition = _materialized_value(materialized, operation.condition)
    true_value = _materialized_value(materialized, operation.true_value)
    false_value = _materialized_value(materialized, operation.false_value)
    result = _switch(context.group, condition, false_value, true_value, x, y)
    _store_result(materialized, operation.result, result)



def _lower_vector_literal(context, operation, materialized, x, y):
    """Materialize one normalized compile-time Vector with existing topology."""
    result = _combine_xyz_mixed(context.group, list(operation.components), x, y)
    _store_result(materialized, operation.result, result)


def _lower_object_property(context, operation, materialized, x, y):
    """Materialize one validated Object property through the exact ObjectValue binding."""
    value = _materialized_value(materialized, operation.value)
    if not isinstance(value, ObjectValue):
        raise CompileError(
            f"Internal error: Semantic IR Object property expected ObjectValue, got {type(value).__name__}"
        )
    result = resolve_object_property_explicit(
        context.group,
        value,
        operation.property_name,
        transform_space=operation.transform_space,
        as_instance=operation.as_instance,
        x=x,
        y=y,
    )
    _store_result(materialized, operation.result, result)

def _lower_vector_component(context, operation, materialized, x, y):
    """Materialize one Vector component projection."""
    value = _materialized_value(materialized, operation.value)
    result = _separate_xyz(context.group, value, operation.component, x, y)
    _store_result(materialized, operation.result, result)



def _slot_value(slot, operands):
    """Resolve one normalized const/runtime call option against materialized operands."""
    if slot is None:
        return None
    mode, payload = slot
    if mode == "const":
        return payload
    if mode == "runtime":
        try:
            return operands[payload]
        except IndexError as exc:
            raise CompileError("Internal error: Call IR runtime option index is out of range") from exc
    raise CompileError(f"Internal error: unknown Call IR option mode {mode!r}")


def _store_call_results(materialized, operation, backend_result):
    """Store one scalar or structural backend call result into declared IR results."""
    if isinstance(backend_result, TupleValue):
        values = tuple(backend_result.values)
    elif isinstance(backend_result, NodeResult):
        values = tuple(backend_result.get_output(name) for name in backend_result.output_names)
    elif isinstance(backend_result, tuple):
        values = tuple(backend_result)
    else:
        values = (backend_result,)
    if len(values) != len(operation.results):
        raise CompileError(
            f"Internal error: Call IR {operation.target.name!r} produced {len(values)} values for {len(operation.results)} results"
        )
    for result, value in zip(operation.results, values):
        _store_result(materialized, result, value)


def _lower_builtin_call(context, operation, operands, x, y):
    """Materialize one AST-free compiler-owned builtin Call IR operation."""
    name = operation.target.name
    options = dict(operation.options)
    group = context.group

    if name == "position":
        return _field_position(group, x, y)
    if name == "normal":
        return _field_normal(group, x, y)
    if name == "index":
        return _field_index(group, x, y)
    if name == "id":
        return _field_id(group, x, y)
    if name == "vector":
        components = [_slot_value(slot, operands) for slot in options["components"]]
        return _combine_xyz_mixed(group, components, x, y)
    if "vector_operation" in options:
        result_type = operation.results[0].typ
        return _vector_math(group, options["vector_operation"], operands, result_type, x, y)

    if name == "empty_geometry":
        return _empty_geometry(group, x, y)
    if name == "points":
        return _points_geometry(group, _slot_value(options["count"], operands), x, y)
    if name == "point":
        return _point_geometry(group, _slot_value(options["slots"][0], operands), x, y)
    if name == "line":
        slots = options["slots"]
        return _line_geometry(group, _slot_value(slots[0], operands), _slot_value(slots[1], operands), x, y)
    if name == "grid":
        slots = options["slots"]
        result = _grid_geometry(group, _slot_value(slots[0], operands), _slot_value(slots[1], operands), x, y)
        if not isinstance(result, tuple) or len(result) != 2:
            raise CompileError("Internal error: grid backend must return Geometry and UV values")
        if result[0].typ is not TYPE_GEOMETRY or result[1].typ is not TYPE_VECTOR:
            raise CompileError("Internal error: grid backend result types do not match Call IR")
        return result
    if name == "set_position":
        selection = operands[2] if len(operands) > 2 else None
        return _set_position_geometry(group, operands[0], operands[1], selection=selection, x=x, y=y)
    if name == "capture_attribute":
        selection = operands[2] if len(operands) > 2 else None
        return _capture_attribute_geometry(
            group,
            operands[0],
            operands[1],
            selection=selection,
            domain=options.get("domain", "POINT"),
            data_type=options.get("data_type"),
            x=x,
            y=y,
        )
    if name == "store_named_attribute":
        name_mode = options["name_mode"]
        attr_name = _slot_value(name_mode, operands)
        value_index = 2 if name_mode[0] == "runtime" else 1
        value = operands[value_index]
        selection = operands[value_index + 1] if len(operands) > value_index + 1 else None
        return _store_named_attribute_geometry(
            group,
            operands[0],
            attr_name,
            value,
            selection=selection,
            domain=options.get("domain", "POINT"),
            data_type_override=options.get("data_type"),
            x=x,
            y=y,
        )
    if name == "set_material":
        return _set_material_geometry(group, operands[0], _slot_value(options["material"], operands), x, y)
    if name == "cube":
        return _cube_geometry(group, _slot_value(options["size"], operands), x, y)
    if name == "join":
        return _join_geometry(group, operands, x, y)
    if name == "transform":
        return _transform_geometry(
            group,
            operands[0],
            translation=_slot_value(options.get("translation"), operands),
            scale=_slot_value(options.get("scale"), operands),
            rotation=_slot_value(options.get("rotation"), operands),
            x=x,
            y=y,
        )
    if name == "polyline":
        return _polyline_geometry(group, [tuple(point) for point in options["points"]], x, y)
    if name == "instance_on_points":
        selection = None
        for index, argument in enumerate(operation.arguments):
            if argument.parameter_name == "selection":
                selection = operands[index]
                break
        return _instance_on_points(
            group,
            operands[0],
            operands[1],
            selection=selection,
            scale=_slot_value(options.get("scale"), operands),
            rotation=_slot_value(options.get("rotation"), operands),
            realize=options.get("realize", True),
            x=x,
            y=y,
        )
    if name == "realize_instances":
        return _realize_instances(group, operands[0], x, y)
    if name == "bundle":
        return build_bundle(group, list(zip(options["item_names"], operands)), x=x, y=y)
    if name == "bundle_get":
        return build_bundle_get(group, operands[0], operands[1], options["item_type"], x=x, y=y)
    if name == "bundle_set":
        return build_bundle_set(group, operands[0], operands[1], operands[2], x=x, y=y)
    if name == "node":
        raw_inputs = {}
        for socket_name, spec in options.get("inputs", ()):
            mode, payload = spec
            if mode == "literal":
                raw_inputs[socket_name] = payload
            elif mode == "runtime":
                raw_inputs[socket_name] = operands[payload]
            elif mode == "multi":
                raw_inputs[socket_name] = [operands[index] for index in payload]
            else:
                raise CompileError(f"Internal error: unknown raw-node input mode {mode!r}")
        outputs = options.get("outputs")
        return build_materialized_raw_node(
            group,
            bl_idname=options["bl_idname"],
            props=dict(options.get("props", ())),
            inputs=raw_inputs,
            output=options.get("output"),
            typ=options.get("typ"),
            outputs=dict(outputs) if outputs is not None else None,
            x=x,
            y=y,
            context="node()",
        )
    raise CompileError(f"Internal error: no Blender lowering for builtin Call IR {name!r}")


def _lower_call(context, operation, materialized, x, y):
    """Materialize one typed Call IR operation without source AST or Compiler state."""
    operands = [_materialized_value(materialized, argument.value) for argument in operation.arguments]
    if operation.target.kind is IRCallableKind.BUILTIN:
        result = _lower_builtin_call(context, operation, operands, x, y)
    else:
        raise CompileError(f"Internal error: unsupported Call IR target kind {operation.target.kind}")
    _store_call_results(materialized, operation, result)

def _execute_operation(context, operation, materialized, base_depth, runtime_bindings=None):
    """Dispatch one ordered IR operation to its Blender realization helper."""
    runtime_bindings = context.runtime_bindings if runtime_bindings is None else runtime_bindings
    effective_depth = base_depth + operation.depth
    x, y = _position(effective_depth)

    if isinstance(operation, IRLiteral):
        _lower_literal(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRBinding):
        _lower_binding(context, operation, materialized, runtime_bindings)
        return
    if isinstance(operation, IRUnary):
        _lower_unary(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRBinary):
        _lower_binary(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRBoolBinary):
        _lower_bool_binary(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRCompare):
        _lower_compare(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRConditional):
        _lower_conditional(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRVectorLiteral):
        _lower_vector_literal(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRObjectProperty):
        _lower_object_property(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRVectorComponent):
        _lower_vector_component(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRCall):
        _lower_call(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRContextRead):
        value = context.group_context_values.get(operation.slot)
        if not isinstance(value, Value):
            raise CompileError(f"Internal error: unavailable backend group context {operation.slot.value}")
        _store_result(materialized, operation.result, value)
        return
    if isinstance(operation, IRContextWrite):
        value = _materialized_value(materialized, operation.value)
        context.group_context_values[operation.slot] = value
        return
    raise CompileError(f"Internal error: unsupported Semantic IR operation {type(operation).__name__}")



def _lower_program_materialized(context, program, runtime_bindings, base_depth):
    """Execute one expression program once and return its program-local materializations."""
    materialized = {}
    for operation in program.operations:
        _execute_operation(context, operation, materialized, base_depth, runtime_bindings)
    return materialized


def _lower_program_result(context, program, runtime_bindings, base_depth):
    """Execute one scalar expression program against an explicit body runtime map."""
    materialized = _lower_program_materialized(context, program, runtime_bindings, base_depth)
    return _materialized_value(materialized, program.result)


def _require_body_value_type(value, expected_type):
    """Reject impossible backend/body semantic type mismatches before publication."""
    if not isinstance(value, Value):
        raise CompileError("Internal error: IRBody expression did not materialize a runtime Value")
    if value.typ is not expected_type:
        raise CompileError(
            f"Internal error: IRBody backend type {value.typ} does not match semantic type {expected_type}"
        )
    return value


@dataclass(frozen=True)
class BodyLoweringResult:
    """Return body outputs plus the final backend binding map for recursive control flow."""

    explicit_outputs: tuple[tuple[str, Value], ...]
    auto_output: tuple[str, Value] | None
    runtime_bindings: Mapping[BindingId, Value]
    group_context_values: Mapping[GroupContextSlot, Value]

    def __post_init__(self) -> None:
        """Freeze detached backend binding and contextual-state snapshots."""
        object.__setattr__(self, "runtime_bindings", MappingProxyType(dict(self.runtime_bindings)))
        object.__setattr__(self, "group_context_values", MappingProxyType(dict(self.group_context_values)))


def _coerce_branch_value(value: Value, target_type):
    """Apply the characterized Repeat Int/Float logical retag without creating a node."""
    if target_type is None or value.typ is target_type:
        return value
    return Value(value.socket, target_type)


def _lower_body_internal(
    context,
    body,
    runtime_bindings,
    base_depth,
    *,
    group_input=None,
    repeat_origin=None,
    control_depth=0,
    legacy_index_override=None,
):
    """Recursively lower one validated structured IRBody against a detached binding map."""
    runtime_bindings = dict(runtime_bindings)
    explicit_outputs = []
    auto_output = None
    for index, statement in enumerate(body.statements):
        layout_index = index if legacy_index_override is None else legacy_index_override
        if isinstance(statement, IRInputDeclaration):
            if group_input is None:
                raise CompileError("Internal error: body input declaration requires Group Input context")
            value, _iface = _create_group_input_socket(
                context.group,
                group_input,
                statement.display_name,
                statement.typ,
                statement.default,
                declaration_id=statement.declaration_id,
            )
            runtime_bindings[statement.target_binding_id] = value
            auto_output = (statement.target_name, value)
            continue
        if isinstance(statement, IRAssign):
            value = _require_body_value_type(
                _lower_program_result(context, statement.value, runtime_bindings, base_depth),
                statement.value.result.typ,
            )
            runtime_bindings[statement.binding_id] = value
            auto_output = (statement.source_name, value)
            continue
        if isinstance(statement, IRBindLeaves):
            materialized = _lower_program_materialized(context, statement.value, runtime_bindings, base_depth)
            for binding in statement.bindings:
                value = _require_body_value_type(_materialized_value(materialized, binding.source), binding.typ)
                runtime_bindings[binding.destination] = value
            auto_output = None
            continue
        if isinstance(statement, IRDiscardExpression):
            _lower_program_materialized(context, statement.value, runtime_bindings, base_depth)
            auto_output = None
            continue
        if isinstance(statement, IROutput):
            value = _require_body_value_type(
                _lower_program_result(context, statement.value, runtime_bindings, base_depth),
                statement.value.result.typ,
            )
            explicit_outputs.append((statement.name, value))
            auto_output = None
            continue
        if isinstance(statement, IRFinalExpression):
            value = _require_body_value_type(
                _lower_program_result(context, statement.value, runtime_bindings, base_depth),
                statement.value.result.typ,
            )
            auto_output = ("out", value)
            continue
        if isinstance(statement, IRPanelDeclaration):
            if group_input is None:
                raise CompileError("Internal error: panel declaration requires Group Input context")
            sockets = []
            for binding_id in statement.member_binding_ids:
                value = runtime_bindings.get(binding_id)
                iface_item = interface_item_for_group_input_value(context.group, group_input, value)
                if iface_item is None:
                    raise CompileError("Internal error: semantic panel member is not a physical Group Input")
                sockets.append(iface_item)
            _create_interface_panel(context.group, sockets, statement.name, collapsed=statement.collapsed)
            auto_output = None
            continue
        if isinstance(statement, IRIf):
            condition = _require_body_value_type(
                _lower_program_result(context, statement.condition, runtime_bindings, base_depth),
                TYPE_BOOL,
            )
            branch_kwargs = {
                "group_input": group_input,
                "repeat_origin": repeat_origin,
                "control_depth": control_depth + 1 if repeat_origin is not None else control_depth,
                "legacy_index_override": layout_index if repeat_origin is None else None,
            }
            true_result = _lower_body_internal(
                context, statement.true_body, dict(runtime_bindings), base_depth + 1, **branch_kwargs
            )
            false_result = _lower_body_internal(
                context, statement.false_body, dict(runtime_bindings), base_depth + 1, **branch_kwargs
            )
            if repeat_origin is None:
                switch_x = 360 + layout_index * 160
                switch_y = -220 - layout_index * 70
            else:
                switch_x = repeat_origin[0] + 680 + control_depth * 120
                switch_y = repeat_origin[1] - 220
            for merge in statement.merges:
                try:
                    true_value = true_result.runtime_bindings[merge.binding_id]
                    false_value = false_result.runtime_bindings[merge.binding_id]
                except KeyError as exc:
                    raise CompileError("Internal error: IRIf merge binding missing from branch backend state") from exc
                true_value = _coerce_branch_value(true_value, merge.true_coerce_to)
                false_value = _coerce_branch_value(false_value, merge.false_coerce_to)
                if true_value.typ is not merge.typ or false_value.typ is not merge.typ:
                    raise CompileError("Internal error: IRIf backend merge type does not match semantic contract")
                merged = _switch(
                    context.group,
                    condition,
                    false_value,
                    true_value,
                    switch_x,
                    switch_y,
                )
                runtime_bindings[merge.binding_id] = merged
                auto_output = (merge.source_name, merged)
            continue
        if isinstance(statement, IRRepeat):
            iterations = _require_body_value_type(
                _lower_program_result(context, statement.iterations, runtime_bindings, base_depth),
                TYPE_INT,
            )
            state_specs = tuple((state.input_type, state.source_name) for state in statement.states)
            if repeat_origin is None:
                repeat_x = 300 + layout_index * 160
                repeat_y = -380 - layout_index * 70
            else:
                repeat_x = repeat_origin[0] + 360 + control_depth * 180
                repeat_y = repeat_origin[1] - 500 - control_depth * 280
            ri, ro = _create_repeat_zone(
                context.group,
                state_specs,
                statement.iteration_name,
                repeat_x,
                repeat_y,
            )
            context.group.links.new(iterations.socket, ri.inputs[0])
            for state in statement.states:
                try:
                    initial = runtime_bindings[state.binding_id]
                except KeyError as exc:
                    raise CompileError(f"Internal error: missing Repeat entry state {state.source_name!r}") from exc
                if initial.typ is not state.input_type:
                    raise CompileError("Internal error: Repeat backend entry type does not match semantic input type")
                context.group.links.new(initial.socket, _socket_by_name(ri.inputs, state.source_name))

            body_bindings = dict(runtime_bindings)
            for state in statement.states:
                body_bindings[state.binding_id] = Value(_socket_by_name(ri.outputs, state.source_name), state.input_type)
            body_bindings[statement.iteration_binding_id] = Value(ri.outputs[0], TYPE_INT)
            body_result = _lower_body_internal(
                context,
                statement.body,
                body_bindings,
                base_depth + 1,
                group_input=group_input,
                repeat_origin=(repeat_x, repeat_y),
                control_depth=0,
            )
            for state in statement.states:
                try:
                    final_value = body_result.runtime_bindings[state.binding_id]
                except KeyError as exc:
                    raise CompileError(f"Internal error: missing Repeat exit state {state.source_name!r}") from exc
                if final_value.typ is not state.input_type and {final_value.typ, state.input_type} != {TYPE_INT, TYPE_FLOAT}:
                    raise CompileError("Internal error: Repeat backend exit type violates semantic state contract")
                context.group.links.new(final_value.socket, _socket_by_name(ro.inputs, state.source_name))
                output_value = Value(_socket_by_name(ro.outputs, state.source_name), state.output_type)
                if state.publish_to_parent:
                    runtime_bindings[state.binding_id] = output_value
                    auto_output = (state.source_name, output_value)
            continue
        raise CompileError(f"Internal error: unsupported IRBody statement {type(statement).__name__}")
    return BodyLoweringResult(
        tuple(explicit_outputs),
        auto_output,
        runtime_bindings,
        context.group_context_values,
    )


def lower_body(context, body, initial_runtime_bindings, base_depth=1, *, group_input=None):
    """Materialize one fully validated structured IRBody without publishing locals to Compiler."""
    if not isinstance(body, IRBody):
        raise TypeError("body must be an IRBody")
    return _lower_body_internal(
        context,
        body,
        initial_runtime_bindings,
        base_depth,
        group_input=group_input,
    )


def _materialize_program_result(materialized, result):
    """Reconstruct one legacy backend expression result from Semantic IR structure."""
    if isinstance(result, IRArray):
        return [_materialize_program_result(materialized, item) for item in result.items]
    if isinstance(result, IRTuple):
        return TupleValue(tuple(_materialized_value(materialized, item) for item in result.items))
    if isinstance(result, IRNamedOutputs):
        return NodeResult({name: _materialized_value(materialized, item) for name, item in result.items})
    return _materialized_value(materialized, result)

def lower_expression(context, program, base_depth=0):
    """Execute one ordered Semantic IR program through an explicit Blender context."""
    materialized = {}
    for operation in program.operations:
        _execute_operation(context, operation, materialized, base_depth)

    # STRUCTURAL_SEMANTICS_LEGACY_EXPRESSION_RESULT_BRIDGE: Migrated IRBody consumes tuple/named-output
    # structure through compiler-owned leaf BindingIds and never requires TupleValue/NodeResult, but
    # legacy whole-body statement lowering still calls expression lowering and expects list/TupleValue/
    # NodeResult return containers. Reconstruct them only at this legacy expression return boundary.
    # Remove this bridge when compile_statement() is no longer a production body path and no caller
    # above Blender lowering consumes backend structural containers.
    return _materialize_program_result(materialized, program.result)


__all__ = ["BlenderIRLoweringContext", "BodyLoweringResult", "lower_expression", "lower_body"]
