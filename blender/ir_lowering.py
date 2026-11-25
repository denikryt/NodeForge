"""Materialize value-based NodeForge Semantic IR into Blender Geometry Nodes."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ..semantic.constants import TYPE_BOOL, TYPE_COLOR, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_ROTATION, TYPE_STRING, TYPE_VECTOR
from ..nf_types import NFType
from ..type_capabilities import REPEAT_ITEM_TYPES
from ..errors import CompileError
from ..compiler_identities import BindingId, InputDeclarationId
from ..semantic.group_context import GroupContextSlot
from .nodes import _boolean_math, _combine_xyz_mixed, _compare, _integer_math, _math, _new_node, _separate_xyz, _switch, _vector_math
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
    _sample_index_geometry,
    _store_named_attribute_geometry,
    _transform_geometry,
)
from .nodes import _id as _field_id, _index as _field_index, _normal as _field_normal, _position as _field_position
from .bundle import build_bundle, build_bundle_get, build_bundle_set
from .object_info import resolve_object_property_explicit
from .raw_nodes import build_materialized_raw_node, read_existing_raw_group_input
from .extension_backend import lower_extension_call
from ..semantic.ir import (
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
from .values import ObjectValue, Value, DeferredConstant, make_value
from .input_delivery import deliver_input, require_output
from ..semantic.callable_contracts import source_argument_type_matches
from ..semantic.ir import IRFunctionMaterializationMode
from .interface import _create_group_input_socket, _create_interface_panel, _set_socket_default
from ..function_instances import (
    FUNCTION_INSTANCE_KEY_PROP,
    direct_library_owner_scope_for_id,
    function_materialization_owner_scope,
    function_group_owner_scope,
    inline_semantic_fingerprint,
)
from .function_materializer import FunctionMaterializationContext, FunctionMaterializer
from ..semantic.source_callables import SourceCallablePreparationKey


def _repeat_item_type_for_nf_type(typ: NFType) -> str:
    """Map one supported semantic type to the exact Blender Repeat item token."""
    try:
        return REPEAT_ITEM_TYPES[typ]
    except KeyError as exc:
        raise CompileError(f"repeat_range state has unsupported type {typ}") from exc


def _socket_by_name(sockets, name):
    """Return the Repeat Zone socket with the given display name."""
    for socket in sockets:
        if socket.name == name:
            return socket
    raise CompileError(f"Internal error: missing Repeat Zone socket {name!r}")


def _create_repeat_zone(group, state_specs, index_name, x=0, y=0):
    """Create one physical Repeat Zone from already-typed semantic state specs."""
    ri = _new_node(group, "GeometryNodeRepeatInput", x, y)
    ro = _new_node(group, "GeometryNodeRepeatOutput", x + 1120, y)
    if not ri.pair_with_output(ro):
        raise CompileError("Could not pair Repeat Zone nodes")
    ro.repeat_items.clear()

    # Semantic analysis validates compiler-owned names. Blender system socket names
    # remain a physical backend fact and are checked only after the zone exists.
    system_socket_names = {"Iterations", "Iteration"}
    for sockets in (ri.inputs, ri.outputs, ro.inputs, ro.outputs):
        for socket in sockets:
            system_socket_names.add(socket.name)
    if index_name in system_socket_names:
        raise CompileError(
            f"repeat_range loop index name {index_name!r} conflicts with Repeat Zone socket name"
        )
    names = [name for _typ, name in state_specs]
    if len(names) != len(set(names)):
        raise CompileError("repeat_range state names must be unique")
    for name in names:
        if name in system_socket_names:
            raise CompileError(
                f"repeat_range state name {name!r} conflicts with Repeat Zone socket name"
            )
    for typ, name in state_specs:
        ro.repeat_items.new(_repeat_item_type_for_nf_type(typ), name)
    return ri, ro


@dataclass
class RootInputEndpoint:
    """One mutable root-only Group Input selection for a compilation attempt."""

    node: object | None = None
    created_by_attempt: bool = False
    existing_input: object | None = None  # Read-only original; never exposed as a stage node.


@dataclass(frozen=True)
class BlenderIRLoweringContext:
    """Explicit Blender backend inputs for one Semantic IR lowering operation.

    Source semantic analysis must not receive this context. It contains only the
    target Geometry Nodes group and already-materialized runtime bindings needed
    to realize a validated Semantic IR program in Blender.
    """

    group: object
    runtime_bindings: Mapping[BindingId, Value | DeferredConstant]
    group_context_values: Mapping[GroupContextSlot, Value] = None
    interface_input_items: dict | None = None
    source_callable_session: object | None = None
    function_materializer: FunctionMaterializer | None = None
    function_materialization_context: FunctionMaterializationContext | None = None
    helper_namespace: str = "Group"
    extension_registry: object | None = None
    generated_resource_transaction: object | None = None
    layout_origin: tuple[float, float] = (0.0, 0.0)
    root_input_endpoint: RootInputEndpoint | None = None

    def __post_init__(self):
        """Freeze value bindings while retaining explicit mutable backend-owned maps."""
        object.__setattr__(self, "runtime_bindings", MappingProxyType(dict(self.runtime_bindings)))
        initial = {} if self.group_context_values is None else dict(self.group_context_values)
        if not all(isinstance(slot, GroupContextSlot) and isinstance(value, Value) for slot, value in initial.items()):
            raise TypeError("group_context_values must map GroupContextSlot to Value")
        object.__setattr__(self, "group_context_values", initial)
        interface_items = {} if self.interface_input_items is None else self.interface_input_items
        if not isinstance(interface_items, dict):
            raise TypeError("interface_input_items must be a mutable dict")
        object.__setattr__(self, "interface_input_items", interface_items)
        if len(self.layout_origin) != 2:
            raise ValueError("layout_origin must be a two-coordinate tuple")


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
    if not isinstance(value, (Value, DeferredConstant)):
        raise CompileError(
            f"Internal error: Semantic IR value v{result.id} has invalid backend type {type(value).__name__}"
        )
    if value.typ != result.typ:
        raise CompileError(
            f"Internal error: Semantic IR value v{result.id} expected type {result.typ}, got {value.typ}"
        )
    materialized[result.id] = value


def _lower_literal(context, operation, materialized, x, y):
    """Keep a typed IR literal detached until a physical input/output needs it."""
    result = DeferredConstant(operation.result.typ, operation.value)
    _store_result(materialized, operation.result, result)


def _lower_binding(context, operation, materialized, runtime_bindings):
    """Resolve one semantic runtime binding to its backend materialization."""
    value = runtime_bindings.get(operation.binding_id)
    if not isinstance(value, (Value, DeferredConstant)):
        raise CompileError(
            f"Internal error: Semantic IR binding {operation.binding_id!r} has no backend operand"
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
        if operation.operand.typ is TYPE_INT:
            result = _integer_math(context.group, "NEGATE", [operand], x, y)
        elif operation.operand.typ is TYPE_FLOAT:
            zero = DeferredConstant(TYPE_FLOAT, 0.0)
            result = _math(context.group, "SUBTRACT", [zero, operand], x, y)
        elif operation.operand.typ == TYPE_VECTOR:
            minus_one = DeferredConstant(TYPE_FLOAT, -1.0)
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
    numeric_types = {TYPE_FLOAT, TYPE_INT}
    if operation.left.typ in numeric_types and operation.right.typ in numeric_types:
        if operation.result.typ is TYPE_INT:
            if operation.left.typ is not TYPE_INT or operation.right.typ is not TYPE_INT:
                raise CompileError("Internal error: Int numeric result requires Int operands")
            integer_operation = {
                "ADD": "ADD",
                "SUBTRACT": "SUBTRACT",
                "MULTIPLY": "MULTIPLY",
                "MODULO": "FLOORED_MODULO",
                "FLOOR_DIVIDE": "DIVIDE_FLOOR",
            }.get(operation.op)
            if integer_operation is None:
                raise CompileError(
                    f"Internal error: unsupported Int Semantic IR binary operation {operation.op!r}"
                )
            result = _integer_math(context.group, integer_operation, [left, right], x, y)
        elif operation.result.typ is TYPE_FLOAT:
            if operation.op == "FLOOR_DIVIDE":
                quotient = _math(context.group, "DIVIDE", [left, right], x, y)
                result = _math(context.group, "FLOOR", [quotient], x, y)
            elif operation.op == "MODULO":
                result = _math(context.group, "FLOORED_MODULO", [left, right], x, y)
            else:
                result = _math(context.group, operation.op, [left, right], x, y)
        else:
            raise CompileError(
                f"Internal error: scalar numeric IR has non-numeric result type {operation.result.typ}"
            )
    elif operation.op in {"ADD", "SUBTRACT"} and operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_VECTOR:
        result = _vector_math(context.group, operation.op, [left, right], TYPE_VECTOR, x, y)
    elif operation.op == "MULTIPLY":
        if operation.left.typ == TYPE_VECTOR and operation.right.typ in numeric_types:
            result = _vector_math(context.group, "SCALE", [left, right], TYPE_VECTOR, x, y)
        elif operation.left.typ in numeric_types and operation.right.typ == TYPE_VECTOR:
            result = _vector_math(context.group, "SCALE", [right, left], TYPE_VECTOR, x, y)
        elif operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_VECTOR:
            result = _vector_math(context.group, "MULTIPLY", [left, right], TYPE_VECTOR, x, y)
        else:
            raise CompileError(
                f"Internal error: unsupported Semantic IR binary lowering for "
                f"{operation.left.typ} {operation.op} {operation.right.typ}"
            )
    elif operation.op == "DIVIDE" and operation.left.typ == TYPE_VECTOR and operation.right.typ in numeric_types:
        inv = _math(context.group, "DIVIDE", [DeferredConstant(TYPE_FLOAT, 1.0), right], x, y)
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
    """Materialize one conditional IR operation with the established socket ordering."""
    condition = _materialized_value(materialized, operation.condition)
    true_value = _materialized_value(materialized, operation.true_value)
    false_value = _materialized_value(materialized, operation.false_value)
    result = _switch(context.group, condition, false_value, true_value, x, y)
    _store_result(materialized, operation.result, result)



def _lower_vector_literal(context, operation, materialized, x, y):
    """Keep a literal Vector detached, preserving its typed components."""
    result = DeferredConstant(TYPE_VECTOR, tuple(operation.components))
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
    """Store one scalar or ordered tuple backend call result into declared IR results."""
    if isinstance(backend_result, tuple):
        values = backend_result
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
        if all(type(component) in {int, float} or isinstance(component, DeferredConstant)
               and component.typ in {TYPE_FLOAT, TYPE_INT} for component in components):
            return DeferredConstant(TYPE_VECTOR, tuple(
                component.value if isinstance(component, DeferredConstant) else component
                for component in components
            ))
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
            require_output(group, operands[1], context="capture_attribute() field"),
            selection=require_output(group, selection, context="capture_attribute() selection") if selection is not None else None,
            domain=options["domain"],
            data_type=options["data_type"],
            x=x,
            y=y,
        )
    if name == "sample_index":
        return _sample_index_geometry(
            group,
            operands[0],
            require_output(group, operands[1], context="sample_index() field"),
            _slot_value(options["index"], operands),
            domain=options["domain"],
            clamp=options["clamp"],
            x=x,
            y=y,
        )
    if name == "store_named_attribute":
        name_mode = options["name_mode"]
        attr_name = _slot_value(name_mode, operands)
        value_index = 2 if name_mode[0] == "runtime" else 1
        value = operands[value_index]
        selection = operands[value_index + 1] if len(operands) > value_index + 1 else None
        if selection is not None:
            selection = require_output(group, selection, context="store_named_attribute() selection")
        return _store_named_attribute_geometry(
            group,
            operands[0],
            attr_name,
            value,
            selection=selection,
            domain=options["domain"],
            data_type_override=options["data_type"],
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
            translation=_slot_value(options["translation"], operands),
            scale=_slot_value(options["scale"], operands),
            rotation=_slot_value(options["rotation"], operands),
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
            selection=require_output(group, selection, context="instance_on_points() selection") if selection is not None else None,
            scale=_slot_value(options["scale"], operands),
            rotation=_slot_value(options["rotation"], operands),
            realize=options["realize"],
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
        raw_inputs = []
        for selector, spec in options["inputs"]:
            mode, payload = spec
            if mode == "literal":
                value_spec = payload
            elif mode == "runtime":
                value_spec = operands[payload]
            elif mode == "multi":
                value_spec = [operands[index] for index in payload]
            else:
                raise CompileError(f"Internal error: unknown raw-node input mode {mode!r}")
            raw_inputs.append((selector, value_spec))
        outputs = options["outputs"]
        endpoint = getattr(context, 'root_input_endpoint', None)
        if options["bl_idname"] == 'NodeGroupInput' and endpoint is not None:
            if endpoint.node is None:
                # Pre-existing stage inputs could only have been inserted by a
                # backend without source authorization. Do not adopt them.
                if any(item.bl_idname == 'NodeGroupInput' for item in group.nodes):
                    raise CompileError('Root stage contains an unauthorized Group Input')
                if endpoint.existing_input is None:
                    result = build_materialized_raw_node(
                        group, bl_idname='NodeGroupInput', props=dict(options['props']),
                        inputs=raw_inputs, output=options['output'], typ=options['typ'],
                        outputs=list(outputs) if outputs is not None else None,
                        x=x, y=y, context='node()',
                    )
                    endpoint.node = result[0].socket.node if isinstance(result, tuple) else result.socket.node
                    endpoint.created_by_attempt = True
                    return result
                from .group_backend import _copy_node_properties
                node = group.nodes.new('NodeGroupInput')
                _copy_node_properties(endpoint.existing_input, node)
                endpoint.node = node
                endpoint.created_by_attempt = False
            return read_existing_raw_group_input(
                endpoint.node, props=dict(options['props']), inputs=raw_inputs,
                output=options['output'], typ=options['typ'],
                outputs=list(outputs) if outputs is not None else None,
                context='node()',
            )
        return build_materialized_raw_node(
            group,
            bl_idname=options["bl_idname"],
            props=dict(options["props"]),
            inputs=raw_inputs,
            output=options["output"],
            typ=options["typ"],
            outputs=list(outputs) if outputs is not None else None,
            x=x,
            y=y,
            context="node()",
        )
    raise CompileError(f"Internal error: no Blender lowering for builtin Call IR {name!r}")


def _source_call_owner_scope(operation):
    """Return the final physical owner already selected by source-call semantics."""
    function_id = operation.target.function_id
    if operation.materialization is not None:
        if operation.materialization.callee != function_id:
            raise CompileError("Internal error: source Call IR materialization target mismatch")
        return function_materialization_owner_scope(operation.materialization)
    if function_id is None or function_id.namespace != "local":
        raise CompileError("Internal error: source Call IR without materialization must be Local catalog")
    return direct_library_owner_scope_for_id(function_id)


def _inline_runtime_value(context, expected, actual, x, y):
    """Detach physical Values while retaining deferred arguments until use."""
    if not isinstance(actual, (Value, DeferredConstant)) or not source_argument_type_matches(expected, actual.typ):
        raise CompileError(f"Internal error: inline argument expects {expected}, got {getattr(actual, 'typ', None)}")
    if expected is TYPE_FLOAT and actual.typ is TYPE_INT:
        if isinstance(actual, DeferredConstant):
            return DeferredConstant(TYPE_FLOAT, actual.value)
        return _math(context.group, "ADD", [actual, DeferredConstant(TYPE_FLOAT, 0.0)], x, y)
    if isinstance(actual, Value):
        return make_value(actual.socket, actual.typ)
    return actual


def _inline_parameter_is_direct_single_sink(body, declaration_id):
    """Prove one parameter reaches at most one supported, immediate IR consumer.

    Only the prepared typed body is inspected. Loops/branches, nested source
    forwarding, output-only values, and multiple uses retain one shared physical
    producer per invocation (#59). This does not build a new flattened IR pass.
    """
    declarations = (stmt for stmt in body.statements if isinstance(stmt, IRInputDeclaration))
    binding_id = next((stmt.target_binding_id for stmt in declarations
                       if stmt.declaration_id == declaration_id), None)
    if binding_id is None:
        return False
    consumers = 0
    for stmt in body.statements:
        if isinstance(stmt, (IRIf, IRRepeat)):
            return False
        program = getattr(stmt, "value", None)
        if program is None or not hasattr(program, "operations"):
            continue
        refs = [op.result.id for op in program.operations
                if isinstance(op, IRBinding) and op.binding_id == binding_id]
        if not refs:
            continue
        if len(refs) > 1 or getattr(program.result, "id", None) in refs:
            return False
        ref = refs[0]
        used = 0
        for op in program.operations:
            if isinstance(op, IRBinding):
                continue
            if isinstance(op, IRCall):
                hits = [index for index, arg in enumerate(op.arguments) if arg.value.id == ref]
                if hits:
                    if op.target.kind is not IRCallableKind.BUILTIN or len(hits) != 1:
                        return False
                    if op.target.name not in {
                        "node", "vector", "cube", "points", "point", "line", "grid",
                        "set_position", "transform", "instance_on_points", "bundle",
                        "bundle_get", "bundle_set", "store_named_attribute", "sample_index",
                    } and "vector_operation" not in dict(op.options):
                        return False
                    if op.target.name == "node":
                        raw = dict(op.options).get("inputs", ())
                        if sum(spec == ("runtime", hits[0]) for _selector, spec in raw) != 1:
                            return False
                    used += 1
                continue
            for attribute in ("operand", "left", "right", "condition", "true_value", "false_value", "value"):
                if getattr(getattr(op, attribute, None), "id", None) == ref:
                    if not isinstance(op, (IRUnary, IRBinary, IRBoolBinary, IRCompare,
                                           IRConditional, IRVectorComponent)):
                        return False
                    used += 1
        # A reference without an admitted consumer may be part of a structural
        # expression; avoid assuming that such a value is unused.
        if used != 1:
            return False
        consumers += used
        if consumers > 1:
            return False
    return consumers <= 1


def _inline_parameter_bindings(context, operation, operands, prepared, x, y):
    """Bind physical interface positions to exact declaration identities once."""
    contract = prepared.contract
    interface = prepared.group.interface
    by_position = {}
    for parameter in contract.parameters:
        position = parameter.input_index
        if position >= len(interface.inputs) or position in by_position:
            raise CompileError("Internal error: inline callable has an invalid physical input index")
        origin = interface.inputs[position].interface_origin
        if origin is None or not isinstance(origin, InputDeclarationId):
            raise CompileError("Internal error: inline parameter lacks a declaration identity")
        if parameter.interface_origin is not None and parameter.interface_origin != origin:
            raise CompileError("Internal error: inline callable parameter declaration identity mismatch")
        by_position[position] = (parameter, interface.inputs[position], origin)

    specified = {}
    if len(operation.arguments) != len(operands):
        raise CompileError("Internal error: inline call runtime operand count changed")
    for arg, value in zip(operation.arguments, operands):
        position = arg.parameter_index
        if position not in by_position or position in specified:
            raise CompileError("Internal error: inline call has invalid or duplicated runtime input position")
        specified[position] = ("runtime", value)
    for arg in operation.static_arguments:
        position = arg.parameter_index
        if position not in by_position or position in specified:
            raise CompileError("Internal error: inline call has invalid or duplicated static input position")
        specified[position] = ("static", arg.value)

    bound = {}
    for position, (parameter, interface_input, origin) in by_position.items():
        mode, arg = specified.get(position, ("default", interface_input.default))
        if mode == "default" and not interface_input.has_default:
            raise CompileError(f"Internal error: missing required inline input {parameter.display_name!r}")
        offset = position * 75
        if mode == "runtime":
            value = _inline_runtime_value(context, parameter.typ, arg, x - 180, y - offset)
        else:
            value = DeferredConstant(parameter.typ, arg)
        if isinstance(value, DeferredConstant) and not _inline_parameter_is_direct_single_sink(
            prepared.group.body, origin
        ):
            if value.typ is TYPE_STRING:
                # Literal-only Menu sockets need the known String even when
                # other consumers require a shared physical String producer.
                value.share_runtime_consumers()
            else:
                # Eager realization preserves non-String sharing through
                # consumers that rewrap Bool or unpack numeric arguments.
                value = require_output(context.group, value,
                                       context=f"inline argument {parameter.display_name}")
        if origin in bound:
            raise CompileError("Internal error: inline declaration was bound more than once")
        bound[origin] = value

    # Neither the source AST order nor panel visual order is a valid binding key.
    from ..semantic.ir import IRInputDeclaration
    declarations = tuple(statement for statement in prepared.group.body.statements
                         if isinstance(statement, IRInputDeclaration))
    if len(declarations) != len(bound) or {item.declaration_id for item in declarations} != set(bound):
        raise CompileError("Internal error: inline body input declarations disagree with its interface")
    runtime_bindings = {item.target_binding_id: bound[item.declaration_id] for item in declarations}
    if len(runtime_bindings) != len(declarations):
        raise CompileError("Internal error: inline body has repeated runtime binding identities")
    return runtime_bindings


def _lower_inline_source_call(context, operation, operands, prepared, x, y):
    """Evaluate one typed prepared body directly inside its immediate caller tree."""
    inputs = _inline_parameter_bindings(context, operation, operands, prepared, x, y)
    materialization = operation.materialization
    if materialization is None or materialization.call_site is None:
        raise CompileError("Internal error: inline call has no occurrence identity")
    ordinal = materialization.call_site.ordinal
    # Each sibling call receives its own deterministic vertical lane, including
    # calls reached from another bound region. No ephemeral node group is needed.
    child_origin = (x + 260, y - 380 - ordinal * 640)
    bound_context = BlenderIRLoweringContext(
        group=context.group,
        runtime_bindings={},
        group_context_values={slot: make_value(value.socket, value.typ)
                              for slot, value in context.group_context_values.items()},
        interface_input_items={},
        source_callable_session=context.source_callable_session,
        function_materializer=context.function_materializer,
        function_materialization_context=context.function_materialization_context,
        helper_namespace=context.helper_namespace,
        extension_registry=context.extension_registry,
        generated_resource_transaction=context.generated_resource_transaction,
        layout_origin=child_origin,
        root_input_endpoint=context.root_input_endpoint,
    )
    result = lower_body(bound_context, prepared.group.body, inputs, base_depth=0,
                        physical_outputs=False)
    outputs = result.explicit_outputs if result.explicit_outputs else (
        (result.auto_output,) if result.auto_output is not None else ()
    )
    if len(outputs) != len(prepared.contract.outputs):
        raise CompileError("Internal error: inline body result count disagrees with source contract")
    values = []
    for (_name, value), output in zip(outputs, prepared.contract.outputs):
        if value.typ is not output.typ:
            raise CompileError("Internal error: inline return type disagrees with source contract")
        physical = require_output(context.group, value, context="inline function return", allow_native_conversion=True)
        values.append(make_value(physical.socket, physical.typ))
    trace = getattr(context.function_materialization_context, "function_compilation_trace", None)
    frame = getattr(trace, "current", None)
    if frame is not None:
        # A region belongs to its currently materialized physical parent, so
        # nested inline and grouped dependencies share that parent's trace.
        frame.record_inline_dependency(operation.materialization, inline_semantic_fingerprint(prepared))
        for owner_key, fingerprint in prepared.group.extension_dependencies:
            frame.record_dependency_identity(
                function_group_owner_scope("EXTENSION", *owner_key), fingerprint,
            )
    return tuple(values)


def _lower_source_call(context, operation, operands, x, y):
    """Materialize one prepared source callable and wire a GeometryNodeGroup by positions."""
    session = context.source_callable_session
    materializer = context.function_materializer
    materialization_context = context.function_materialization_context
    function_id = operation.target.function_id
    if session is None or materializer is None or materialization_context is None or function_id is None:
        raise CompileError("Internal error: source Call IR reached backend without source-call services")
    inline = (operation.materialization is not None
              and operation.materialization.mode is IRFunctionMaterializationMode.INLINE)
    if inline:
        prepared_callable = session.get_inline_prepared(operation.materialization)
        from ..compiler_identities import inline_expansion_owner_scope
        owner_scope = inline_expansion_owner_scope(operation.materialization.call_site)
    else:
        owner_scope = _source_call_owner_scope(operation)
        prepared_callable = session.get_prepared(SourceCallablePreparationKey(function_id, owner_scope))
    if prepared_callable.group.identity.owner_scope != owner_scope:
        raise CompileError("Internal error: prepared source callable owner mismatch")
    contract = prepared_callable.contract
    if contract.function_id != function_id:
        raise CompileError("Internal error: prepared source callable FunctionId mismatch")
    if len(contract.outputs) != len(operation.results):
        raise CompileError("Internal error: source-call result arity changed before Blender lowering")
    names = tuple(output.result_name for output in contract.outputs)
    expected_names = names if names and names[0] is not None else None
    if operation.result_names != expected_names:
        raise CompileError("Internal error: source-call result names changed before Blender lowering")
    for result, output in zip(operation.results, contract.outputs):
        if result.typ is not output.typ:
            raise CompileError("Internal error: source-call result type changed before Blender lowering")

    if inline:
        return _lower_inline_source_call(context, operation, operands, prepared_callable, x, y)

    from .library_groups import (
        _input_sockets as _group_node_inputs,
        _output_sockets as _group_node_outputs,
        apply_function_node_display_name,
        materialize_prepared_library_callable,
    )

    if function_id.kind == "MODULE_DEF":
        if operation.materialization is None:
            raise CompileError("Module definition call requires a shared/unique materialization policy")
        materialized_group = materializer.materialize_module(
            prepared_callable, operation.materialization, materialization_context,
        )
    elif function_id.kind == "LOCAL_DEF":
        if operation.materialization is None:
            raise CompileError("Internal error: script-local function call requires materialization policy")
        from .local_functions import build_prepared_local_materialization_spec

        spec = build_prepared_local_materialization_spec(
            prepared_callable,
            operation.materialization,
            helper_namespace=context.helper_namespace,
        )
        materialized_group = materializer.materialize_local(spec, materialization_context)
    else:
        record = prepared_callable.source_record
        if record is None:
            raise CompileError("Internal error: prepared library callable lost its resolved source record")
        materialized_group = materialize_prepared_library_callable(
            record,
            materializer,
            prepared_callable,
            materialization=operation.materialization,
            materialization_context=materialization_context,
        )

    node = context.group.nodes.new("GeometryNodeGroup")
    node.location = (x, y)
    node.node_tree = materialized_group.group
    apply_function_node_display_name(node, materialized_group.group)
    physical_inputs = _group_node_inputs(node)
    for static in operation.static_arguments:
        if static.parameter_index >= len(physical_inputs):
            raise CompileError("Internal error: source-call static input position is out of range")
        deliver_input(context.group, physical_inputs[static.parameter_index],
                      DeferredConstant(static.typ, static.value) if hasattr(static, "typ") else static.value,
                      context="grouped static argument")
    for argument, value in zip(operation.arguments, operands):
        position = argument.parameter_index
        if position is None or position >= len(physical_inputs):
            raise CompileError("Internal error: source-call runtime input position is out of range")
        deliver_input(context.group, physical_inputs[position], value, context="grouped function argument")

    physical_outputs = _group_node_outputs(node)
    values = []
    for result, output in zip(operation.results, contract.outputs):
        if output.index >= len(physical_outputs):
            raise CompileError("Internal error: source-call output position is out of range")
        values.append(make_value(physical_outputs[output.index], result.typ))
    if operation.materialization is not None and operation.materialization.mode.value == "UNIQUE":
        node[FUNCTION_INSTANCE_KEY_PROP] = materialized_group.instance_key
    return tuple(values)


def _lower_call(context, operation, materialized, x, y):
    """Materialize one typed Call IR operation without source AST or Compiler state."""
    operands = [_materialized_value(materialized, argument.value) for argument in operation.arguments]
    if operation.target.kind is IRCallableKind.BUILTIN:
        result = _lower_builtin_call(context, operation, operands, x, y)
    elif operation.target.kind is IRCallableKind.SOURCE_FUNCTION:
        result = _lower_source_call(context, operation, operands, x, y)
    elif operation.target.kind is IRCallableKind.EXTENSION:
        from ..semantic.ir import IRExtensionMaterializationMode
        if (operation.extension_materialization is not None and
                operation.extension_materialization.mode is IRExtensionMaterializationMode.GROUPED):
            from .extension_group_materializer import materialize_extension_call
            result = materialize_extension_call(context, operation,
                                               [require_output(context.group, v, context="grouped extension input") for v in operands], x, y)
        else:
            result = lower_extension_call(context, operation,
                                          [require_output(context.group, v, context="extension input") for v in operands], x, y)
    else:
        raise CompileError(f"Internal error: unsupported Call IR target kind {operation.target.kind}")
    _store_call_results(materialized, operation, result)

def _execute_operation(context, operation, materialized, base_depth, runtime_bindings=None):
    """Dispatch one ordered IR operation to its Blender realization helper."""
    runtime_bindings = context.runtime_bindings if runtime_bindings is None else runtime_bindings
    effective_depth = base_depth + operation.depth
    x, y = _position(effective_depth)
    x += context.layout_origin[0]
    y += context.layout_origin[1]

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
        context.group_context_values[operation.slot] = require_output(context.group, value, context="group context")
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
    if not isinstance(value, (Value, DeferredConstant)):
        raise CompileError("Internal error: IRBody expression did not produce a backend operand")
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
    runtime_bindings: Mapping[BindingId, Value | DeferredConstant]
    group_context_values: Mapping[GroupContextSlot, Value]

    def __post_init__(self) -> None:
        """Freeze detached backend binding and contextual-state snapshots."""
        object.__setattr__(self, "runtime_bindings", MappingProxyType(dict(self.runtime_bindings)))
        object.__setattr__(self, "group_context_values", MappingProxyType(dict(self.group_context_values)))


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
                # Inline parameters are already validated and keyed by BindingId.
                value = runtime_bindings.get(statement.target_binding_id)
                if not isinstance(value, (Value, DeferredConstant)) or value.typ is not statement.typ:
                    raise CompileError("Internal error: missing or incompatible prebound inline input")
                auto_output = (statement.target_name, value)
                continue
            value, _iface = _create_group_input_socket(
                context.group,
                group_input,
                statement.display_name,
                statement.typ,
                statement.default,
                declaration_id=statement.declaration_id,
            )
            runtime_bindings[statement.target_binding_id] = value
            context.interface_input_items[statement.declaration_id] = _iface
            auto_output = (statement.target_name, value)
            continue
        if isinstance(statement, IRAssign):
            value = _require_body_value_type(
                _lower_program_result(context, statement.value, runtime_bindings, base_depth),
                statement.value.result.typ,
            )
            if statement.value.result.typ is TYPE_VECTOR and isinstance(value, DeferredConstant):
                # A named Vector constructor is an intentional source identity (#50).
                if any(isinstance(op, IRVectorLiteral) or
                       isinstance(op, IRCall) and op.target.name == "vector"
                       for op in statement.value.operations):
                    value = require_output(context.group, value, context="named Vector")
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
                auto_output = None
                continue
            sockets = []
            for origin in statement.member_origins:
                iface_item = context.interface_input_items.get(origin)
                if iface_item is None:
                    raise CompileError("Internal error: semantic panel origin has no physical Group Input item")
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
            switch_x += context.layout_origin[0]
            switch_y += context.layout_origin[1]
            for merge in statement.merges:
                try:
                    true_value = true_result.runtime_bindings[merge.binding_id]
                    false_value = false_result.runtime_bindings[merge.binding_id]
                except KeyError as exc:
                    raise CompileError("Internal error: IRIf merge binding missing from branch backend state") from exc
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
            state_specs = tuple((state.typ, state.source_name) for state in statement.states)
            if repeat_origin is None:
                repeat_x = 300 + layout_index * 160
                repeat_y = -380 - layout_index * 70
            else:
                repeat_x = repeat_origin[0] + 360 + control_depth * 180
                repeat_y = repeat_origin[1] - 500 - control_depth * 280
            repeat_x += context.layout_origin[0]
            repeat_y += context.layout_origin[1]
            ri, ro = _create_repeat_zone(
                context.group,
                state_specs,
                statement.iteration_name,
                repeat_x,
                repeat_y,
            )
            iterations = require_output(context.group, iterations, context="Repeat iterations")
            context.group.links.new(iterations.socket, ri.inputs[0])
            for state in statement.states:
                try:
                    initial = runtime_bindings[state.binding_id]
                except KeyError as exc:
                    raise CompileError(f"Internal error: missing Repeat entry state {state.source_name!r}") from exc
                if initial.typ is not state.typ:
                    raise CompileError("Internal error: Repeat backend entry type does not match semantic input type")
                initial = require_output(context.group, initial, context="Repeat entry state")
                context.group.links.new(initial.socket, _socket_by_name(ri.inputs, state.source_name))

            body_bindings = dict(runtime_bindings)
            for state in statement.states:
                body_bindings[state.binding_id] = Value(_socket_by_name(ri.outputs, state.source_name), state.typ)
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
                if final_value.typ is not state.typ:
                    raise CompileError("Internal error: Repeat backend exit type violates semantic state contract")
                final_value = require_output(context.group, final_value, context="Repeat exit state")
                context.group.links.new(final_value.socket, _socket_by_name(ro.inputs, state.source_name))
                output_value = Value(_socket_by_name(ro.outputs, state.source_name), state.typ)
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


def lower_body(context, body, initial_runtime_bindings, base_depth=1, *, group_input=None,
               physical_outputs=True):
    """Lower one body; expose physical outputs for group assembly boundaries."""
    if not isinstance(body, IRBody):
        raise TypeError("body must be an IRBody")
    result = _lower_body_internal(context, body, initial_runtime_bindings, base_depth, group_input=group_input)
    if not physical_outputs:
        return result
    outputs = tuple((name, require_output(context.group, v, context="Group Output", allow_native_conversion=True))
                    for name, v in result.explicit_outputs)
    auto = ((result.auto_output[0], require_output(context.group, result.auto_output[1],
             context="Group auto output", allow_native_conversion=True)) if result.auto_output is not None else None)
    return BodyLoweringResult(outputs, auto, result.runtime_bindings, result.group_context_values)


def _materialize_program_result(materialized, result):
    """Reconstruct one IR expression result using ordinary Python structure."""
    if isinstance(result, IRArray):
        return [_materialize_program_result(materialized, item) for item in result.items]
    if isinstance(result, IRTuple):
        return tuple(_materialized_value(materialized, item) for item in result.items)
    if isinstance(result, IRNamedOutputs):
        return {name: _materialized_value(materialized, item) for name, item in result.items}
    return _materialized_value(materialized, result)

def lower_expression(context, program, base_depth=0):
    """Execute one ordered Semantic IR program through an explicit Blender context."""
    materialized = {}
    for operation in program.operations:
        _execute_operation(context, operation, materialized, base_depth)

    return _materialize_program_result(materialized, program.result)


__all__ = ["BlenderIRLoweringContext", "BodyLoweringResult", "lower_expression", "lower_body"]
