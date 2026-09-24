"""Shared physical Blender socket typing for compiler backends."""

from __future__ import annotations

from .errors import CompileError
from .nf_types import NFType


_SOCKET_IDNAME_BY_NF_TYPE = {
    NFType.FLOAT: "NodeSocketFloat",
    NFType.VECTOR: "NodeSocketVector",
    NFType.BOOL: "NodeSocketBool",
    NFType.GEOMETRY: "NodeSocketGeometry",
    NFType.INT: "NodeSocketInt",
    NFType.MATERIAL: "NodeSocketMaterial",
    NFType.OBJECT: "NodeSocketObject",
    NFType.STRING: "NodeSocketString",
    NFType.BUNDLE: "NodeSocketBundle",
    NFType.ROTATION: "NodeSocketRotation",
}


def socket_type_for_nf_type(typ: NFType) -> str:
    """Return the canonical Blender interface/runtime socket idname for one NFType."""
    if not isinstance(typ, NFType):
        raise TypeError("typ must be an NFType")
    return _SOCKET_IDNAME_BY_NF_TYPE[typ]


def runtime_socket_nf_type(socket) -> NFType | None:
    """Return the exact canonical runtime type represented by a Blender node socket."""
    bl_idname = getattr(socket, "bl_idname", "") or ""
    for typ, socket_idname in _SOCKET_IDNAME_BY_NF_TYPE.items():
        if bl_idname.startswith(socket_idname):
            return typ
    return None


def runtime_socket_owner_tree(socket):
    """Return the owning node tree for an ordinary node socket, or ``None``."""
    node = getattr(socket, "node", None)
    if node is None:
        return None
    return getattr(node, "id_data", None)


def validate_runtime_value_socket(
    socket,
    expected_type: NFType,
    expected_group,
    *,
    context: str,
    require_output: bool = True,
):
    """Validate one physical runtime-value socket at a compiler-owned boundary."""
    if not isinstance(expected_type, NFType):
        raise TypeError("expected_type must be an NFType")
    physical_type = runtime_socket_nf_type(socket)
    if physical_type is None:
        bl_idname = getattr(socket, "bl_idname", "<unknown>")
        raise CompileError(f"{context}: unsupported Blender runtime socket type {bl_idname!r}")
    if physical_type is not expected_type:
        raise CompileError(
            f"{context}: physical socket type is {physical_type.value}, expected {expected_type.value}"
        )
    if require_output and getattr(socket, "is_output", None) is not True:
        raise CompileError(f"{context}: runtime values must reference ordinary output sockets")
    if runtime_socket_owner_tree(socket) is not expected_group:
        raise CompileError(f"{context}: runtime socket does not belong to the current Geometry Nodes group")
    return socket


__all__ = [
    "socket_type_for_nf_type",
    "runtime_socket_nf_type",
    "runtime_socket_owner_tree",
    "validate_runtime_value_socket",
]
