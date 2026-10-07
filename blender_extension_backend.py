"""Physical realization of typed EXTENSION Call IR in Blender."""

from __future__ import annotations

import inspect
from collections import OrderedDict

from .blender_socket_types import validate_runtime_value_socket
from .errors import CompileError
from .extension_api import ExtensionBackendContext, ExtensionBackendValue
from .extension_contracts import ExtensionCallableSpec, TypeSpec
from .extension_values import unpack_value
from .semantic.ir import IRCallOperandRef
from .nf_types import NFType
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


def _bound_arguments(spec: ExtensionCallableSpec, operation, operands, group) -> inspect.BoundArguments:
    """Reconstruct canonical public BoundArguments from static/runtime IR carriers."""
    signature = spec.python_signature()
    by_position: dict[int, list[tuple[int | None, object]]] = {}
    for argument, value in zip(operation.arguments, operands):
        if argument.parameter_index is None:
            raise CompileError("Internal error: extension runtime operand has no parameter position")
        by_position.setdefault(argument.parameter_index, []).append(
            (argument.variadic_index, _physical_argument_value(value, group))
        )
    for argument in operation.static_arguments:
        by_position.setdefault(argument.parameter_index, []).append((argument.variadic_index, argument.value))

    arguments = OrderedDict()
    parameters = tuple(signature.parameters.values())
    for index, parameter in enumerate(parameters):
        items = by_position.get(index, [])
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
    """Validate exact selected result shape and convert it to internal backend Values."""
    if result_spec.kind == "NF_SET":
        expected = next(iter(result_spec.nf_types))
        return _validate_result_leaf(value, expected, context)
    if not isinstance(value, tuple) or len(value) != len(result_spec.items):
        raise CompileError(
            f"Extension implementation must return a {len(result_spec.items)}-item tuple"
        )
    return tuple(
        _validate_result_leaf(item, next(iter(item_spec.nf_types)), context)
        for item, item_spec in zip(value, result_spec.items)
    )


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
