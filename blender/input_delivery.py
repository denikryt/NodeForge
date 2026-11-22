"""Consumer-aware delivery of typed values to resolved Blender input sockets.

A socket default is a physical representation only for a conventional visible
input. Hidden, contextual, unsupported and multi-input destinations require
explicit producers and links rather than assuming Blender's unconnected state.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import CompileError
from ..nf_types import NFType
from ..semantic.callable_contracts import canonicalize_group_input_default
from .socket_types import runtime_socket_nf_type, validate_runtime_value_socket
from .values import ConstantPayload, DeferredConstant, Value


@dataclass(frozen=True)
class InputRealization:
    """Report the actual physical realization for raw metadata and tests."""

    mode: str
    default: object | None = None
    links: int = 0


def _same_value(expected, actual) -> bool:
    """Compare a canonical numeric/tuple default with Blender's RNA readback."""
    if isinstance(expected, tuple):
        try:
            actual_parts = tuple(actual)
        except TypeError:
            return False
        return len(expected) == len(actual_parts) and all(
            _same_value(a, b) for a, b in zip(expected, actual_parts)
        )
    if type(expected) is bool:
        return type(actual) is bool and expected == actual
    if type(expected) is int:
        return type(actual) is int and expected == actual
    if type(expected) is float:
        return type(actual) in {int, float} and abs(float(actual) - expected) <= 1e-6
    return expected == actual


def _input_type(socket):
    """Return the physical input type without guessing from display labels."""
    return runtime_socket_nf_type(socket)


def _interface_has_contextual_fallback(socket) -> bool:
    """Detect non-VALUE defaults of a physical GeometryNodeGroup call input."""
    node = getattr(socket, "node", None)
    if getattr(node, "bl_idname", None) != "GeometryNodeGroup":
        return False
    tree = getattr(node, "node_tree", None)
    interface = getattr(tree, "interface", None)
    if interface is None:
        return True
    identifier = getattr(socket, "identifier", None)
    for item in interface.items_tree:
        if (
            getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "in_out", None) == "INPUT"
            and getattr(item, "identifier", None) == identifier
        ):
            return getattr(item, "default_input", "VALUE") != "VALUE"
    # The physical input is not reliably backed by a visible interface item.
    return True


def _is_safe_default(socket) -> bool:
    """Conservatively recognize ordinary visible writable scalar input defaults."""
    if bool(getattr(socket, "is_multi_input", False)):
        return False
    if bool(getattr(socket, "is_unavailable", False)) or not bool(getattr(socket, "enabled", True)):
        return False
    if bool(getattr(socket, "hide_value", False)) or _interface_has_contextual_fallback(socket):
        return False
    return hasattr(socket, "default_value")


def _constant_from(operand, socket, context: str) -> DeferredConstant:
    """Normalize a deferred carrier or raw literal against one resolved input."""
    if isinstance(operand, DeferredConstant):
        return operand
    if isinstance(operand, ConstantPayload):
        return DeferredConstant(operand.typ, operand.value)
    typ = _input_type(socket)
    if typ is None:
        raise CompileError(f"{context}: unsupported input socket type {getattr(socket, 'bl_idname', None)!r}")
    return DeferredConstant(typ, operand)


def _validate_type(socket, typ: NFType, context: str):
    """Require exact typing except for the preexisting Int→Float widening."""
    physical = _input_type(socket)
    if physical is None:
        raise CompileError(f"{context}: unsupported input socket type {getattr(socket, 'bl_idname', None)!r}")
    if typ != physical and not (typ is NFType.INT and physical is NFType.FLOAT):
        raise CompileError(f"{context}: input expects {physical.value}, got {typ.value}")


def validate_input(group, socket, operand, *, context: str = "node input"):
    """Preflight a resolved socket/operand without adding links or default values."""
    if getattr(socket, "is_output", False):
        raise CompileError(f"{context}: destination must be an input socket")
    node = getattr(socket, "node", None)
    if getattr(node, "id_data", None) is not group:
        raise CompileError(f"{context}: input belongs to a different node tree")
    if bool(getattr(socket, "is_unavailable", False)) or not bool(getattr(socket, "enabled", True)):
        raise CompileError(f"{context}: input socket is unavailable")
    if isinstance(operand, Value):
        _validate_type(socket, operand.typ, context)
        validate_runtime_value_socket(operand.socket, operand.typ, group, context=context)
        return operand
    constant = _constant_from(operand, socket, context)
    _validate_type(socket, constant.typ, context)
    if constant.typ is NFType.INT and _input_type(socket) is NFType.FLOAT:
        # Widen only for the physical input, preserving the source carrier type.
        canonicalize_group_input_default(NFType.FLOAT, constant.value)
    return constant


def require_output(group, operand, *, context: str = "constant") -> Value:
    """Return a physical output, allocating one typed producer per deferred owner."""
    if isinstance(operand, Value):
        validate_runtime_value_socket(operand.socket, operand.typ, group, context=context)
        return operand
    if not isinstance(operand, DeferredConstant):
        raise CompileError(f"{context}: expected a typed backend operand")
    operand.bind_group(group)
    if operand._materialized is not None:
        return operand._materialized
    from .nodes import (
        _bool_value, _color_value, _int_value, _rotation_value,
        _string_value, _value, _vector_value,
    )

    producers = {
        NFType.FLOAT: _value,
        NFType.INT: _int_value,
        NFType.BOOL: _bool_value,
        NFType.VECTOR: _vector_value,
        NFType.COLOR: _color_value,
        NFType.ROTATION: _rotation_value,
        NFType.STRING: _string_value,
    }
    producer = producers.get(operand.typ)
    if producer is None:
        raise CompileError(f"{context}: cannot materialize {operand.typ.value} constant")
    value = producer(group, operand.value)
    operand._materialized = value
    return value


def deliver_input(group, socket, operand, *, context: str = "node input") -> InputRealization:
    """Write an eligible default or connect a validated physical producer to an input."""
    checked = validate_input(group, socket, operand, context=context)
    if isinstance(checked, Value):
        try:
            group.links.new(checked.socket, socket)
        except Exception as exc:
            raise CompileError(f"{context}: failed to link runtime input: {exc}") from exc
        return InputRealization("single_link", links=1)
    checked.bind_group(group)
    if _is_safe_default(socket):
        default = checked.value
        if checked.typ is NFType.INT and _input_type(socket) is NFType.FLOAT:
            default = canonicalize_group_input_default(NFType.FLOAT, default)
        try:
            socket.default_value = default
        except Exception as exc:
            raise CompileError(f"{context}: failed to assign socket default: {exc}") from exc
        actual = socket.default_value
        if not _same_value(default, actual):
            raise CompileError(f"{context}: Blender did not store an equivalent socket default")
        return InputRealization("literal_default", default=default)
    value = require_output(group, checked, context=context)
    try:
        group.links.new(value.socket, socket)
    except Exception as exc:
        raise CompileError(f"{context}: failed to link constant input: {exc}") from exc
    return InputRealization("single_link", links=1)


__all__ = ["InputRealization", "validate_input", "deliver_input", "require_output"]
