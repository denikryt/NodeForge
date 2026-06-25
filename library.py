"""Function-library discovery, compilation, and node-group call helpers."""

from __future__ import annotations

import os
import re
import sys
import importlib.util
from pathlib import Path

import bpy

from .constants import TYPE_BOOL, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_VECTOR
from .errors import CompileError
from .interface import _set_socket_default
from .nodes import _new_node
from .values import Value
from .systems import registry as systems_registry

_LIBRARY_DIR_NAME = "functions"
_SOURCE_EXTENSIONS = (".nf", ".nodeforge")
_NATIVE_FILE_NAME = "function.py"
_PACKAGE_SOURCE_NAME = "source.nf"


def _library_dir() -> Path:
    """Return the folder that stores NodeForge function packages/scripts."""
    return Path(__file__).resolve().parent / _LIBRARY_DIR_NAME


def _is_valid_function_name(name: str) -> bool:
    """Return True when a file/directory stem can be called as a Python-like function."""
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""))


def display_name_for_function(name: str) -> str:
    """Return the user-facing node title for a library function name."""
    parts = [p for p in re.split(r"[_\s]+", name or "") if p]
    if not parts:
        return name or "Function"
    return "".join(part[:1].upper() + part[1:] for part in parts)


def display_name_for_group(group) -> str:
    """Return a clean title for a generated function node group."""
    for key in ("nodeforge_library_name", "nodeforge_function_module"):
        try:
            value = group.get(key)
        except Exception:
            value = None
        if value:
            return display_name_for_function(str(value))
    name = getattr(group, "name", "") or "Function"
    if name.startswith("NodeForge.fn."):
        parts = name.split(".")
        if len(parts) >= 3:
            return display_name_for_function(parts[2])
    return name


def apply_function_node_display_name(node, function_group) -> None:
    """Set the visible node title to a clean function name instead of an internal id."""
    title = display_name_for_group(function_group)
    try:
        node.name = title
    except Exception:
        pass
    try:
        node.label = title
    except Exception:
        pass


def apply_function_group_display_name(group, function_name: str):
    """Rename a materialized function group to its clean user-facing name when safe."""
    title = display_name_for_function(function_name)
    try:
        if group.name.startswith("NodeForge.fn.") or group.name == function_name or group.name == _safe_group_name(function_name):
            group.name = title
    except Exception:
        pass
    try:
        group["nodeforge_function_display_name"] = title
    except Exception:
        pass
    return group


def _source_path_for_name(name: str) -> Path | None:
    """Find an editable NodeForge source file for a function name.

    Supported layouts:
      functions/foo.nf
      functions/foo.nodeforge
      functions/foo/source.nf
    """
    if not _is_valid_function_name(name):
        return None
    root = _library_dir()
    for ext in _SOURCE_EXTENSIONS:
        path = root / f"{name}{ext}"
        if path.exists() and path.is_file():
            return path
    package_source = root / name / _PACKAGE_SOURCE_NAME
    if package_source.exists() and package_source.is_file():
        return package_source
    return None


def _module_path_for_name(name: str) -> Path | None:
    """Find a packaged native Python helper module for a function name."""
    if not _is_valid_function_name(name):
        return None
    packaged = _library_dir() / name / _NATIVE_FILE_NAME
    if packaged.exists() and packaged.is_file():
        return packaged
    return None


def _record_kind(name: str) -> str:
    """Return a compact UI kind label for a discovered function."""
    has_source = _source_path_for_name(name) is not None
    has_native = _module_path_for_name(name) is not None
    if has_source and has_native:
        return "package"
    if has_native:
        return "native"
    return "script"


def has_module_library_function(name: str) -> bool:
    """Return True if a native Python helper exists for *name*."""
    return _module_path_for_name(name) is not None


def has_native_compile_call(name: str) -> bool:
    """Return True when a function module owns the whole call compilation."""
    if _module_path_for_name(name) is None:
        return False
    module = _load_function_module(name)
    return callable(getattr(module, "compile_call", None))


def backend_builtins_for_function(name: str) -> dict[str, object]:
    """Return package-local backend helpers exposed while compiling source.nf."""
    if _module_path_for_name(name) is None:
        return {}
    module = _load_function_module(name)
    builtins = getattr(module, "BACKEND_BUILTINS", None)
    if builtins is None:
        return {}
    if not isinstance(builtins, dict):
        raise CompileError(f"Function module {name} BACKEND_BUILTINS must be a dict")
    return dict(builtins)


def _load_function_module(name: str):
    """Load a native function helper from the functions folder."""
    path = _module_path_for_name(name)
    if path is None:
        raise CompileError(f"Unknown native library function: {name}")
    package = __package__ or "NodeForge"
    module_name = f"{package}.functions.{name}.function"
    existing = sys.modules.get(module_name)
    if existing is not None and getattr(existing, "__file__", None) == str(path):
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise CompileError(f"Could not load function module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def compile_module_library_function_call(comp, expr, depth=0, function_name=None):
    """Compile a call handled by a native helper in the functions folder."""
    name = function_name or expr.func.id
    module = _load_function_module(name)
    compile_call = getattr(module, "compile_call", None)
    if compile_call is None:
        raise CompileError(f"Function module {name} must define compile_call(comp, expr, depth=0)")
    return compile_call(comp, expr, depth)


def _validate_library_function_name(name: str) -> None:
    """Reject function-library names reserved for embedded system constructors."""
    systems_registry.validate_no_reserved_collision(name, "Library function")


def _validate_library_function_names(names: set[str]) -> set[str]:
    """Reject discovered function-library names reserved for system constructors."""
    for name in sorted(names, key=str.lower):
        _validate_library_function_name(name)
    return names


def library_function_names() -> set[str]:
    """Return all callable function names from the functions folder."""
    root = _library_dir()
    if not root.exists():
        return set()
    names: set[str] = set()
    for path in root.iterdir():
        if path.name == "__init__.py" or path.name.startswith("__"):
            continue
        if path.is_file():
            if path.suffix in _SOURCE_EXTENSIONS and _is_valid_function_name(path.stem):
                names.add(path.stem)
        elif path.is_dir() and _is_valid_function_name(path.name):
            if _source_path_for_name(path.name) is not None or _module_path_for_name(path.name) is not None:
                names.add(path.name)
    return _validate_library_function_names(names)


def has_library_function(name: str) -> bool:
    """Return True if a script source or native helper exists for the function."""
    _validate_library_function_name(name)
    return _source_path_for_name(name) is not None or _module_path_for_name(name) is not None


def load_library_source(name: str) -> str:
    """Load editable NodeForge source code for a library function."""
    _validate_library_function_name(name)
    path = _source_path_for_name(name)
    if path is None:
        raise CompileError(f"Library function {name!r} has no editable .nf source")
    return path.read_text(encoding="utf-8")


def _safe_group_name(name: str) -> str:
    """Create the reusable node-group datablock name for an editable function."""
    return display_name_for_function(name)


def _socket_type_to_value_type(socket) -> str:
    """Map a Blender group socket to a NodeForge semantic type."""
    bl_idname = getattr(socket, "bl_idname", "")
    if bl_idname == "NodeSocketGeometry":
        return TYPE_GEOMETRY
    if bl_idname == "NodeSocketVector":
        return TYPE_VECTOR
    if bl_idname == "NodeSocketBool":
        return TYPE_BOOL
    if bl_idname == "NodeSocketInt":
        return TYPE_INT
    return TYPE_FLOAT


def _normalized_socket_name(name: str) -> str:
    """Normalize socket and keyword names so scale_xy matches 'Scale XY'."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _input_sockets(node):
    """Return real, non-hidden input sockets for a group node."""
    return [s for s in node.inputs if getattr(s, "enabled", True) and not getattr(s, "hide", False)]


def _output_sockets(node):
    """Return real, non-hidden output sockets for a group node."""
    return [s for s in node.outputs if getattr(s, "enabled", True) and not getattr(s, "hide", False)]


def get_or_create_library_group(name: str, compile_group_callback):
    """Compile/update the node group that backs an editable .nf library function."""
    _validate_library_function_name(name)
    source = load_library_source(name)
    backend_builtins = backend_builtins_for_function(name)
    module_path = _module_path_for_name(name)
    backend_signature = str(module_path.stat().st_mtime_ns) if module_path is not None else ""
    group_name = _safe_group_name(name)
    existing = bpy.data.node_groups.get(group_name)
    if existing is not None and getattr(existing, "bl_idname", None) == "GeometryNodeTree":
        try:
            if existing.get("nodeforge_library_source") == source and existing.get("nodeforge_backend_signature") == backend_signature:
                return existing
        except Exception:
            pass
        group = compile_group_callback(source, group_name, existing_group=existing, backend_builtins=backend_builtins)
    else:
        group = compile_group_callback(source, group_name, backend_builtins=backend_builtins)
    try:
        group["nodeforge_library_name"] = name
        group["nodeforge_library_source"] = source
        group["nodeforge_function_kind"] = _record_kind(name)
        group["nodeforge_backend_signature"] = backend_signature
    except Exception:
        pass
    return group


def make_library_call_node(group, function_group, compiled_args, const_args, x=0, y=0):
    """Create a GeometryNodeGroup call to a compiled function node group."""
    node = _new_node(group, "GeometryNodeGroup", x, y)
    node.node_tree = function_group
    apply_function_node_display_name(node, function_group)

    normalized_inputs = {_normalized_socket_name(s.name): s for s in _input_sockets(node)}
    for raw_name, value in const_args.items():
        socket = normalized_inputs.get(_normalized_socket_name(raw_name))
        if socket is None:
            raise CompileError(f"Function {function_group.name} has no input named {raw_name!r}")
        _set_socket_default(socket, value)
    for raw_name, value in compiled_args.items():
        socket = normalized_inputs.get(_normalized_socket_name(raw_name))
        if socket is None:
            raise CompileError(f"Function {function_group.name} has no input named {raw_name!r}")
        group.links.new(value.socket, socket)

    outputs = _output_sockets(node)
    if len(outputs) != 1:
        raise CompileError(f"Library function {function_group.name} must have exactly one output for expression calls")
    return Value(outputs[0], _socket_type_to_value_type(outputs[0]))


def library_function_records() -> list[dict[str, str]]:
    """Return discovered function records with name, kind and path."""
    root = _library_dir()
    if not root.exists():
        return []
    records: list[dict[str, str]] = []
    for name in sorted(library_function_names(), key=str.lower):
        source = _source_path_for_name(name)
        module = _module_path_for_name(name)
        path = source or module or (root / name)
        records.append({"name": name, "kind": _record_kind(name), "path": str(path)})
    return records


def materialize_library_function_group(name: str, compile_group_callback):
    """Create/update the reusable GeometryNodeTree for a library function.

    Native packaged functions may define materialize_group(...). Editable source is
    still stored on the generated group when source.nf exists, so Load Script can
    return a useful user-facing script instead of native Python implementation text.
    """
    _validate_library_function_name(name)
    if _module_path_for_name(name) is not None:
        module = _load_function_module(name)
        materialize = getattr(module, "materialize_group", None)
        if materialize is not None:
            group = materialize(compile_group_callback)
            return apply_function_group_display_name(group, name)
    if _source_path_for_name(name) is not None:
        group = get_or_create_library_group(name, compile_group_callback)
        return apply_function_group_display_name(group, name)
    raise CompileError(f"Unknown library function: {name}")


__all__ = [
    "library_function_names",
    "library_function_records",
    "materialize_library_function_group",
    "has_library_function",
    "load_library_source",
    "get_or_create_library_group",
    "make_library_call_node",
    "display_name_for_function",
    "display_name_for_group",
    "apply_function_node_display_name",
    "has_module_library_function",
    "has_native_compile_call",
    "backend_builtins_for_function",
    "compile_module_library_function_call",
    "_normalized_socket_name",
]
