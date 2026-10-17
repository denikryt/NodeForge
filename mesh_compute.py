"""Blender-independent declaration and buffer contract for Local Mesh Compute.

The compiler inspects declarations statically; only explicitly trusted Python source
is executed later in the physical backend.  No NumPy or Blender import is needed.
"""

from __future__ import annotations

import ast
import inspect
import hashlib
import struct
import keyword
import math
import sys
from array import array
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

from .errors import CompileError


@dataclass(frozen=True)
class MeshComputeDeclaration:
    """Static signature and POINT FLOAT attributes of one Local callback."""

    name: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    parameters: tuple[tuple[str, str, object], ...]


def _numpy_float32(value: object, count: int, label: str, *, xyz: bool = False) -> array | None:
    """Copy a NumPy numeric buffer with C-level finite checks when NumPy is loaded.

    This optional fast path does not import or require NumPy in NodeForge core.
    Plain Python sequences continue through the strict scalar validator.
    """
    np = sys.modules.get("numpy")
    if np is None or not isinstance(value, np.ndarray):
        return None
    if xyz:
        if value.shape not in {(count,), (count // 3, 3)}:
            raise ValueError(f"Invalid Mesh Compute {label}: expected {count} finite float32 values (shape mismatch)")
    elif value.shape != (count,):
        raise ValueError(f"Invalid Mesh Compute {label}: expected {count} finite float32 values (shape mismatch)")
    if value.dtype.kind not in "fiu":
        raise ValueError(f"Invalid Mesh Compute {label}: expected real numeric values")
    # Check the source BEFORE casting (including float64), then check float32
    # overflow AFTER casting. np.ascontiguousarray also handles strided arrays.
    try:
        if not bool(np.isfinite(value).all()):
            raise ValueError("non-finite input value")
        with np.errstate(over="ignore", invalid="ignore"):
            values = np.ascontiguousarray(value, dtype=np.float32).reshape(-1)
        if not bool(np.isfinite(values).all()):
            raise ValueError("non-finite float32 value")
        return array("f", values.tobytes())
    except (TypeError, ValueError, OverflowError, MemoryError) as exc:
        raise ValueError(f"Invalid Mesh Compute {label}: expected {count} finite float32 values ({exc})") from exc


def _finite_float32_buffer(values: array) -> bool:
    """Check a native float32 buffer efficiently if optional NumPy is loaded."""
    np = sys.modules.get("numpy")
    if np is not None:
        return bool(np.isfinite(np.frombuffer(values, dtype=np.float32)).all())
    return all(math.isfinite(value) for value in values)


@dataclass(frozen=True)
class MeshInputs:
    """Copied float32 geometry buffers visible to a Python calculation."""

    positions: array
    point_attributes: Mapping[str, array]
    vertex_count: int

    def __post_init__(self) -> None:
        """Reject incomplete arrays before exposing them to an extension."""
        if not isinstance(self.vertex_count, int) or isinstance(self.vertex_count, bool) or self.vertex_count <= 0:
            raise ValueError("Mesh Compute requires a nonempty vertex set")
        if not isinstance(self.positions, array) or self.positions.typecode != "f" or len(self.positions) != 3 * self.vertex_count:
            raise ValueError("Mesh Compute positions must be a flat float32 XYZ buffer")
        if any(not isinstance(v, array) or v.typecode != "f" or len(v) != self.vertex_count
               for v in self.point_attributes.values()):
            raise ValueError("Mesh Compute POINT FLOAT inputs must contain one value per vertex")
        if not _finite_float32_buffer(self.positions):
            raise ValueError("Mesh Compute input coordinates must be finite")
        if not all(_finite_float32_buffer(buf) for buf in self.point_attributes.values()):
            raise ValueError("Mesh Compute input attributes must be finite")


@dataclass(frozen=True)
class MeshComputeResult:
    """Validated, detached float32 result of a topology-preserving callback."""

    positions: array | None
    point_attributes: Mapping[str, array]


def mesh_compute(*, reads: tuple[str, ...] = (), writes: tuple[str, ...] = ()):
    """Declare a pure, deterministic Mesh → Mesh processing function.

    The callback receives private float32 mesh buffers; returned coordinates and
    declared POINT FLOAT attributes are validated before Blender publication.
    """
    _validate_names(reads, "reads")
    _validate_names(writes, "writes")

    def decorate(callback: Callable):
        """Attach immutable authoring metadata without invoking the callback."""
        if not inspect.isfunction(callback):
            raise TypeError("@mesh_compute must decorate a Python function")
        callback.__nodeforge_mesh_compute__ = (tuple(reads), tuple(writes))
        return callback

    return decorate


def _validate_names(names: object, field: str) -> tuple[str, ...]:
    """Validate exact POINT attribute names without guessing from code output."""
    if not isinstance(names, (tuple, list)) or any(not isinstance(x, str) or not x for x in names):
        raise CompileError(f"mesh_compute {field} must be a tuple/list of nonempty strings")
    if len(names) != len(set(names)):
        raise CompileError(f"mesh_compute {field} contains duplicate attribute names")
    return tuple(names)


def _decorator_call(decorator: ast.expr) -> ast.Call | None:
    """Recognize the explicitly supported authoring spelling, not a dynamic alias."""
    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name) and decorator.func.id == "mesh_compute":
        return decorator
    return None


def has_mesh_compute_declaration(source: str, *, filename: str = "<Local>") -> bool:
    """Check whether a file declares a callable without executing source code."""
    try:
        module = ast.parse(source, filename=filename)
    except (SyntaxError, RecursionError):
        return False
    return any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
               and any(_decorator_call(d) is not None for d in item.decorator_list)
               for item in module.body)


def parse_mesh_compute_source(source: str, *, stem: str, filename: str = "<Local>") -> MeshComputeDeclaration:
    """Parse and enforce the static, single-function Local runtime API contract."""
    try:
        module = ast.parse(source, filename=filename)
    except (SyntaxError, RecursionError) as exc:
        raise CompileError(f"Mesh Compute syntax error in {filename}: {exc}") from exc
    decorated = [(item, d) for item in module.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                 for d in item.decorator_list if _decorator_call(d) is not None]
    if len(decorated) != 1:
        raise CompileError(f"Mesh Compute {filename} must have exactly one top-level @mesh_compute function")
    func, decorator = decorated[0]
    if isinstance(func, ast.AsyncFunctionDef) or func.name != stem or len(func.decorator_list) != 1:
        raise CompileError(f"Mesh Compute callable in {filename} must be a single decorated def named {stem!r}")
    if decorator.args or any(item.arg not in {"reads", "writes"} or item.arg is None for item in decorator.keywords):
        raise CompileError("@mesh_compute accepts only literal reads= and writes= keyword arguments")
    options: dict[str, tuple[str, ...]] = {}
    for item in decorator.keywords:
        if item.arg in options:
            raise CompileError(f"Duplicate @mesh_compute argument: {item.arg}")
        if not isinstance(item.value, (ast.List, ast.Tuple)) or not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in item.value.elts):
            raise CompileError(f"@mesh_compute {item.arg} must be a literal list or tuple of names")
        options[item.arg] = _validate_names(tuple(e.value for e in item.value.elts), item.arg)
    args = func.args
    if args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or len(args.args) < 1:
        raise CompileError("Mesh Compute must have one mesh argument followed by ordinary typed scalar parameters")
    if args.args[0].annotation is not None or args.defaults and len(args.defaults) == len(args.args):
        raise CompileError("The first Mesh Compute argument must be an unannotated, required mesh")
    defaults = [inspect.Parameter.empty] * (len(args.args) - len(args.defaults))
    for d in args.defaults:
        if isinstance(d, ast.Constant) and type(d.value) in {float, int, bool}:
            default = d.value
        elif (isinstance(d, ast.UnaryOp) and isinstance(d.op, ast.USub)
              and isinstance(d.operand, ast.Constant) and type(d.operand.value) in {float, int}):
            default = -d.operand.value
        else:
            raise CompileError("Mesh Compute scalar defaults must be literal float, int or bool")
        defaults.append(default)
    params = []
    for a, default in zip(args.args[1:], defaults[1:]):
        if not isinstance(a.annotation, ast.Name) or a.annotation.id not in {"float", "int", "bool"}:
            raise CompileError(f"Mesh Compute parameter {a.arg!r} needs a builtin float, int or bool annotation")
        typ = a.annotation.id
        if default is not inspect.Parameter.empty and type(default).__name__ != typ:
            raise CompileError(f"Mesh Compute default for {a.arg!r} must be {typ}")
        if not a.arg.isidentifier() or keyword.iskeyword(a.arg):
            raise CompileError(f"Invalid Mesh Compute parameter {a.arg!r}")
        params.append((a.arg, typ, default))
    if len({a.arg for a in args.args}) != len(args.args):
        raise CompileError("Duplicate Mesh Compute parameter name")
    return MeshComputeDeclaration(func.name, options.get("reads", ()), options.get("writes", ()), tuple(params))


def _normalized_floats(value: object, count: int, label: str, *, xyz: bool = False) -> array:
    """Normalize a scalar or XYZ array into detached finite float32 values."""
    accelerated = _numpy_float32(value, count, label, xyz=xyz)
    if accelerated is not None:
        return accelerated
    if isinstance(value, array) and value.typecode == "f":
        if len(value) != count or not _finite_float32_buffer(value):
            raise ValueError(f"Invalid Mesh Compute {label}: expected {count} finite float32 values")
        return array("f", value)
    try:
        if xyz and hasattr(value, "shape") and tuple(value.shape) == (count // 3, 3):
            value = value.reshape(-1)
        elements = list(value)
        if len(elements) != count or any(isinstance(v, bool) or not isinstance(v, (int, float)) and not hasattr(v, "__float__") for v in elements):
            raise ValueError("length or scalar type mismatch")
        normalized = array("f", (float(v) for v in elements))
        if not all(math.isfinite(v) for v in normalized):
            raise ValueError("non-finite float32 value")
        return normalized
    except (TypeError, ValueError, OverflowError, MemoryError) as exc:
        raise ValueError(f"Invalid Mesh Compute {label}: expected {count} finite float32 values ({exc})") from exc


def normalize_mesh_result(raw: object, *, vertex_count: int, writes: tuple[str, ...]) -> MeshComputeResult:
    """Validate a complete callback result before creating any Blender ID."""
    if not isinstance(vertex_count, int) or vertex_count <= 0:
        raise ValueError("Mesh Compute requires a nonempty vertex set")
    if not isinstance(raw, dict) or set(raw) - {"positions", "point_attributes"}:
        raise ValueError("Mesh Compute result must be a dict with positions and/or point_attributes")
    if not raw:
        raise ValueError("Mesh Compute callback returned no outputs")
    pos = (_normalized_floats(raw["positions"], 3 * vertex_count, "positions", xyz=True)
           if "positions" in raw else None)
    attrs = raw.get("point_attributes", {})
    if not isinstance(attrs, dict) or set(attrs) != set(writes):
        raise ValueError("Mesh Compute returned attributes must match declared writes exactly")
    if pos is None and not attrs:
        raise ValueError("Mesh Compute callback returned no outputs")
    normalized = {name: _normalized_floats(attrs[name], vertex_count, name) for name in writes}
    return MeshComputeResult(pos, MappingProxyType(normalized))


def fingerprint_mesh_inputs(
    mesh: MeshInputs,
    *,
    declared_reads: tuple[str, ...],
    parameters: Mapping[str, object],
    implementation_fingerprint: str,
) -> str:
    """Hash only the observable data of a deterministic numerical callback.

    Topology, UV layers, materials and undeclared attributes affect publication
    freshness separately; they cannot change this callback's numerical result.
    """
    if not isinstance(implementation_fingerprint, str) or not implementation_fingerprint:
        raise ValueError("Mesh Compute numerical fingerprint requires a source generation")
    if set(mesh.point_attributes) != set(declared_reads) or len(set(declared_reads)) != len(declared_reads):
        raise ValueError("Mesh Compute numerical inputs do not match declared reads")
    hasher = hashlib.sha256()

    def add(part: bytes) -> None:
        """Hash explicit byte lengths to keep the scalar/buffer encoding unambiguous."""
        hasher.update(len(part).to_bytes(8, 'big'))
        hasher.update(part)

    add(implementation_fingerprint.encode('utf-8'))
    add(str(mesh.vertex_count).encode('ascii'))
    add(mesh.positions.tobytes())
    for name in declared_reads:
        add(name.encode('utf-8'))
        add(mesh.point_attributes[name].tobytes())
    for name, value in sorted(parameters.items()):
        add(name.encode('utf-8'))
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite Mesh Compute parameter {name!r}")
            add(b'f' + struct.pack('<f', value))
        elif type(value) is int:
            add(b'i' + str(value).encode('ascii'))
        elif type(value) is bool:
            add(b'b1' if value else b'b0')
        else:
            raise ValueError(f"Mesh Compute scalar parameter {name!r} must be float, int or bool")
    return hasher.hexdigest()


__all__ = ["mesh_compute", "MeshInputs", "MeshComputeResult", "MeshComputeDeclaration", "parse_mesh_compute_source", "has_mesh_compute_declaration", "normalize_mesh_result", "fingerprint_mesh_inputs"]
