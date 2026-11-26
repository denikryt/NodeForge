"""Physical realization of typed EXTENSION Call IR in Blender."""

from __future__ import annotations

import inspect
from collections import OrderedDict

from .socket_types import validate_runtime_value_socket
from ..errors import CompileError
from ..extension_api import ExtensionBackendContext, ExtensionBackendValue
from ..extensions.contracts import (ExtensionCallableSpec, TypeSpec,
    is_structured_parameter_type_spec, validate_structured_static_leaf)
from ..evaluation_modes import EvaluationMode
from ..extension_result_api import ResultStaticArg
from ..extensions.values import type_spec_accepts_nf
from ..extensions.values import unpack_value
from ..semantic.ir import IRCallOperandRef
from ..nf_types import NFType
from .values import Value, make_value

def _selected_spec(registry, operation) -> ExtensionCallableSpec:
    """Return the semantic alternative already selected before physical lowering."""
    callable_id = operation.target.extension_callable_id
    if callable_id is None:
        raise CompileError("Internal error: EXTENSION Call IR has no callable identity")
    family = registry.callable_specs(callable_id)
    index = operation.target.extension_overload_index
    if len(family) == 1:
        if index is not None:
            raise CompileError("Internal error: non-overloaded extension call carries overload index")
        return family[0]
    if index is None or index >= len(family):
        raise CompileError("Internal error: overloaded extension call has invalid selected alternative")
    return family[index]


def _physical_argument_value(value: Value, group) -> ExtensionBackendValue:
    """Convert one compiler-owned runtime value into the public physical wrapper."""
    if not isinstance(value, Value):
        raise CompileError("Internal error: extension runtime operand is not a Value")
    validate_runtime_value_socket(
        value.socket,
        value.typ,
        group,
        context="extension runtime argument",
        require_output=True,
    )
    return ExtensionBackendValue(value.socket, value.typ)


def _validate_argument_transport(spec, operation, operands, group):
    """Validate selected declaration, slots and physical operands before realization."""
    layout = dict(operation.structured_arguments)
    structured = {i: p for i, p in enumerate(spec.parameters) if is_structured_parameter_type_spec(p.type_spec)}
    if layout.keys() != structured.keys():
        raise CompileError("Structured argument layout does not match selected declaration")
    for index, parameter in structured.items():
        expected_key_type = str if parameter.type_spec.kind == "DICT_STR" else int
        if any(type(key) is not expected_key_type for key in layout[index]):
            raise CompileError("Structured argument key kind does not match selected declaration")
    if len(operation.arguments) != len(operands):
        raise CompileError("Extension runtime operand count does not match Call IR")
    physical_operands = []
    for argument, operand in zip(operation.arguments, operands):
        if not isinstance(operand, Value) or operand.typ is not argument.value.typ:
            raise CompileError("Extension runtime operand type does not match Call IR")
        if argument.parameter_index not in range(len(spec.parameters)):
            raise CompileError("Invalid extension argument parameter position")
        parameter = spec.parameters[argument.parameter_index]
        if argument.parameter_index in structured:
            if (argument.parameter_name != parameter.name
                    or parameter.evaluation_mode is EvaluationMode.COMPILE_TIME_ONLY
                    or not type_spec_accepts_nf(parameter.type_spec.item, operand.typ)):
                raise CompileError("Invalid structured runtime argument mode or type")
        physical_operands.append(_physical_argument_value(operand, group))
    for argument in operation.static_arguments:
        if argument.parameter_index not in range(len(spec.parameters)):
            raise CompileError("Invalid extension static argument position")
        if argument.parameter_index in structured:
            parameter = structured[argument.parameter_index]
            if parameter.evaluation_mode is EvaluationMode.RUNTIME_ONLY:
                raise CompileError("Runtime-only structured argument cannot carry a static value")
            try:
                validate_structured_static_leaf(ResultStaticArg(argument.static_type, argument.value), parameter.type_spec.item)
            except (TypeError, ValueError) as exc:
                raise CompileError(f"Invalid structured static argument: {exc}") from exc
    return physical_operands


def _bound_arguments(spec: ExtensionCallableSpec, operation, operands, group) -> inspect.BoundArguments:
    """Reconstruct canonical public BoundArguments from static/runtime IR carriers."""
    physical_operands = _validate_argument_transport(spec, operation, operands, group)
    signature = spec.python_signature()
    layout = dict(operation.structured_arguments)
    by_position: dict[int, list[tuple[int | None, object]]] = {}
    for argument, value in zip(operation.arguments, physical_operands):
        if argument.parameter_index is None:
            raise CompileError("Internal error: extension runtime operand has no parameter position")
        by_position.setdefault(argument.parameter_index, []).append(
            (argument.variadic_index, value)
        )
    for argument in operation.static_arguments:
        by_position.setdefault(argument.parameter_index, []).append((argument.variadic_index, argument.value))

    arguments = OrderedDict()
    parameters = tuple(signature.parameters.values())
    for index, parameter in enumerate(parameters):
        items = by_position.get(index, [])
        if index in layout:
            keys = layout[index]
            values = [value for _, value in sorted(items)]
            arguments[parameter.name] = (dict(zip(keys, values))
                if spec.parameters[index].type_spec.kind == "DICT_STR" else values)
            continue
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            if any(var_index is None for var_index, _value in items):
                raise CompileError("Internal error: extension variadic parameter lost variadic indices")
            ordered = sorted(items, key=lambda item: int(item[0]))
            arguments[parameter.name] = tuple(value for _var_index, value in ordered)
            continue
        if len(items) != 1 or items[0][0] is not None:
            raise CompileError("Internal error: extension fixed parameter reconstruction is incomplete")
        arguments[parameter.name] = items[0][1]

    return inspect.BoundArguments(signature, arguments)


def _validate_result_leaf(value, expected_type: NFType, context: ExtensionBackendContext):
    """Validate one public result wrapper against semantic and physical backend truth."""
    if not isinstance(value, ExtensionBackendValue):
        raise CompileError("Extension implementation must return ExtensionBackendValue runtime leaves")
    if value.typ is not expected_type:
        raise CompileError(
            f"Extension implementation returned {value.typ.value}, expected {expected_type.value}"
        )
    validate_runtime_value_socket(
        value.socket,
        expected_type,
        context.group,
        context="extension result",
        require_output=True,
    )
    return make_value(value.socket, expected_type)


def _validate_result(value, result_spec: TypeSpec, context: ExtensionBackendContext):
    """Compare physical return shape against the exact typed Call IR result tuple."""
    expected_types = context.result_types
    names = context.result_names
    if result_spec.kind == "NF_SET":
        declared_types = (next(iter(result_spec.nf_types)),)
    elif result_spec.kind == "TUPLE_FIXED":
        declared_types = tuple(next(iter(item.nf_types)) for item in result_spec.items)
    elif result_spec.kind == "NAMED_RESULTS":
        declared_names = tuple(name for name, _ in result_spec.named_items)
        declared_types = tuple(typ for _, typ in result_spec.named_items)
        if names != declared_names:
            raise CompileError("Extension Call IR result names disagree with the selected callable contract")
    elif result_spec.kind == "DYNAMIC_RESULTS":
        declared_types = expected_types
        if names is None and len(expected_types) != 1:
            raise CompileError("Dynamic extension direct Call IR requires exactly one result")
    else:
        raise CompileError("Internal error: non-executable result reached the physical backend")
    if expected_types != declared_types:
        raise CompileError("Extension Call IR results disagree with the selected callable contract")
    if result_spec.kind == "NAMED_RESULTS" or (result_spec.kind == "DYNAMIC_RESULTS" and names is not None):
        if type(value) is not dict or set(value) != set(names):
            raise CompileError(f"Extension implementation must return a dict with exact result keys {names!r}")
        return tuple(_validate_result_leaf(value[name], typ, context)
                     for name, typ in zip(names, expected_types))
    if names is not None:
        raise CompileError("Positional extension Call IR cannot carry named results")
    if result_spec.kind in {"NF_SET", "DYNAMIC_RESULTS"}:
        return _validate_result_leaf(value, expected_types[0], context)
    if not isinstance(value, tuple) or len(value) != len(expected_types):
        raise CompileError(f"Extension implementation must return a {len(expected_types)}-item tuple")
    return tuple(_validate_result_leaf(item, expected_types[index], context)
                 for index, item in enumerate(value))


def lower_extension_call(context, operation, operands, x, y):
    """Resolve and invoke one extension implementation from the current registry snapshot."""
    registry = context.extension_registry
    if registry is None:
        raise CompileError("Internal error: EXTENSION Call IR reached backend without ExtensionRegistry")
    transaction = context.generated_resource_transaction
    if transaction is None:
        raise CompileError("Internal error: EXTENSION Call IR reached backend without generated-resource transaction")

    spec = _selected_spec(registry, operation)
    callable_id = operation.target.extension_callable_id
    backend_context = ExtensionBackendContext(
        group=context.group,
        location=(x, y),
        generated_resource_transaction=transaction,
        result_types=tuple(item.typ for item in operation.results),
        result_names=operation.result_names,
    )
    try:
        if operation.extension_state_type is None:
            bound = _bound_arguments(spec, operation, operands, context.group)
            result = registry.invoke_implementation(
                callable_id, backend_context, *bound.args, **bound.kwargs
            )
        else:
            def resolve_runtime_leaf(stored):
                """Resolve one IR operand reference into the public backend runtime wrapper."""
                if not isinstance(stored, IRCallOperandRef):
                    return None
                if stored.operand_index >= len(operands):
                    raise CompileError("Internal error: extension semantic state operand reference is out of range")
                value = operands[stored.operand_index]
                return value.typ, _physical_argument_value(value, context.group)

            semantic_state = unpack_value(
                operation.extension_state_type,
                operation.extension_state,
                registry=registry,
                runtime_leaf_resolver=resolve_runtime_leaf,
            )
            result = registry.invoke_implementation(callable_id, backend_context, semantic_state)
    except CompileError:
        raise
    except Exception as exc:
        raise CompileError(
            f"Extension implementation {callable_id.name!r} failed: {exc}"
        ) from exc
    return _validate_result(result, spec.result, backend_context)


__all__ = ["lower_extension_call"]
