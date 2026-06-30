"""Namespace-aware library discovery, local storage, and node-group calls."""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import bpy

from .constants import TYPE_BOOL, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_VECTOR
from .errors import CompileError
from .interface import _set_socket_default
from .nodes import _new_node
from .values import Value
from .systems import registry as systems_registry

_SOURCE_EXTENSIONS = (".nf", ".nodeforge")
_PACKAGE_SOURCE_NAME = "source.nf"


@dataclass(frozen=True)
class LibraryCatalog:
    """Description of one public NodeForge library import namespace."""

    namespace: str
    dirname: str
    allow_native: bool
    native_module_file: str | None
    display_label: str


CATALOGS = {
    "functions": LibraryCatalog("functions", "functions", True, "function.py", "Functions"),
    "examples": LibraryCatalog("examples", "examples", True, "backend.py", "Examples"),
    "local": LibraryCatalog("local", "local", False, None, "Local"),
}


@dataclass(frozen=True)
class LibraryEntryRecord:
    """One concrete source/native file that exposes a public catalog name."""

    namespace: str
    name: str
    kind: str
    path: Path
    source_path: Path | None = None
    module_path: Path | None = None
    folder_path: str = ""

    def as_dict(self) -> dict[str, str]:
        """Return a UI/test-friendly dictionary representation."""
        return {
            "namespace": self.namespace,
            "name": self.name,
            "kind": self.kind,
            "path": str(self.path),
            "folder_path": self.folder_path,
        }


def _package_root() -> Path:
    """Return the NodeForge package directory."""
    return Path(__file__).resolve().parent


def _catalog(namespace: str) -> LibraryCatalog:
    """Return a known catalog descriptor or raise a controlled compile error."""
    try:
        return CATALOGS[namespace]
    except KeyError as exc:
        raise CompileError(f"Unknown library catalog: {namespace}") from exc


def _default_local_catalog_dir() -> Path:
    """Return the persistent user-owned root for local catalog sources."""
    return Path(bpy.utils.user_resource("DATAFILES", path="nodeforge/local", create=True))


def catalog_dir(namespace: str) -> Path:
    """Return the filesystem root for a catalog namespace."""
    catalog = _catalog(namespace)
    if catalog.namespace == "local":
        return _default_local_catalog_dir()
    return _package_root() / catalog.dirname


def _is_valid_function_name(name: str) -> bool:
    """Return True when a file/directory stem can be called as a Python-like function."""
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""))


def _is_public_function_name(name: str) -> bool:
    """Return True for callable names exposed to user imports."""
    return _is_valid_function_name(name) and not name.startswith("_")


def _validate_public_entry_name(name: str, context: str = "Library entry") -> str:
    """Return a validated public library entry name."""
    if not _is_public_function_name(name):
        raise CompileError(f"{context} name must be a valid public NodeForge import name")
    systems_registry.validate_no_reserved_collision(name, context)
    return name


def display_name_for_function(name: str) -> str:
    """Return the user-facing node title for a library entry name."""
    parts = [p for p in re.split(r"[_\s]+", name or "") if p]
    if not parts:
        return name or "Function"
    return "".join(part[:1].upper() + part[1:] for part in parts)


def display_name_for_group(group) -> str:
    """Return a clean title for a generated library node group."""
    for key in ("nodeforge_library_name", "nodeforge_function_module"):
        try:
            value = group.get(key)
        except Exception:
            value = None
        if value:
            return display_name_for_function(str(value))
    name = getattr(group, "name", "") or "Function"
    for prefix in ("NodeForge.fn.", "NodeForge.example.", "NodeForge.local."):
        if name.startswith(prefix):
            return display_name_for_function(name[len(prefix):])
    return name


def apply_function_node_display_name(node, function_group) -> None:
    """Set the visible node title to a clean entry name instead of an internal id."""
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
    """Rename legacy function groups to a clean user-facing name when safe."""
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


def _safe_group_name(name: str) -> str:
    """Create the legacy reusable group name for functions catalog entries."""
    return display_name_for_function(name)


def _group_name(namespace: str, name: str) -> str:
    """Return the generated GeometryNodeTree name for a catalog entry."""
    if namespace == "functions":
        return _safe_group_name(name)
    if namespace == "examples":
        return f"NodeForge.example.{name}"
    if namespace == "local":
        return f"NodeForge.local.{name}"
    return f"NodeForge.{namespace}.{name}"


def _immediate_source_path(root: Path, name: str) -> Path | None:
    """Find a non-recursive source file for a bundled catalog entry."""
    for ext in _SOURCE_EXTENSIONS:
        path = root / f"{name}{ext}"
        if path.exists() and path.is_file():
            return path
    package_source = root / name / _PACKAGE_SOURCE_NAME
    if package_source.exists() and package_source.is_file():
        return package_source
    return None


def _immediate_module_path(root: Path, name: str, native_module_file: str | None) -> Path | None:
    """Find a non-recursive native module for a bundled catalog entry."""
    if native_module_file is None:
        return None
    path = root / name / native_module_file
    if path.exists() and path.is_file():
        return path
    return None


def _relative_folder_for_path(root: Path, source_path: Path, name: str) -> str:
    """Return a local UI folder path for a discovered source record."""
    try:
        rel = source_path.relative_to(root)
    except ValueError:
        return ""
    if rel.name == _PACKAGE_SOURCE_NAME:
        parts = rel.parts[:-2]
    else:
        parts = rel.parts[:-1]
    return "/".join(parts)


def _record_kind_for_paths(source_path: Path | None, module_path: Path | None) -> str:
    """Return the public record kind for a source/native path combination."""
    if source_path is not None and module_path is not None:
        return "package"
    if module_path is not None:
        return "native"
    return "script"


def _candidate_records(namespace: str) -> list[LibraryEntryRecord]:
    """Return raw discovered records before duplicate-name reduction."""
    catalog = _catalog(namespace)
    root = catalog_dir(namespace)
    if namespace == "local":
        ensure_local_catalog_dir()
    if not root.exists():
        return []

    records: list[LibraryEntryRecord] = []
    if namespace == "local":
        for path in sorted(root.rglob("*"), key=lambda p: str(p.relative_to(root)).lower()):
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(part.startswith("_") or part.startswith(".") for part in rel_parts):
                continue
            if path.is_file() and path.name == _PACKAGE_SOURCE_NAME:
                raise CompileError(f"Unsupported local source layout: {path.relative_to(root)}")
            if path.is_file() and path.suffix in _SOURCE_EXTENSIONS and _is_public_function_name(path.stem):
                records.append(
                    LibraryEntryRecord(
                        namespace=namespace,
                        name=path.stem,
                        kind="script",
                        path=path,
                        source_path=path,
                        folder_path=_relative_folder_for_path(root, path, path.stem),
                    )
                )
        return records

    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if path.name == "__init__.py" or path.name.startswith("__"):
            continue
        if path.is_file():
            if path.suffix in _SOURCE_EXTENSIONS and _is_public_function_name(path.stem):
                records.append(
                    LibraryEntryRecord(
                        namespace=namespace,
                        name=path.stem,
                        kind="script",
                        path=path,
                        source_path=path,
                    )
                )
        elif path.is_dir() and _is_public_function_name(path.name):
            source_path = _immediate_source_path(root, path.name)
            module_path = _immediate_module_path(root, path.name, catalog.native_module_file if catalog.allow_native else None)
            if source_path is not None or module_path is not None:
                records.append(
                    LibraryEntryRecord(
                        namespace=namespace,
                        name=path.name,
                        kind=_record_kind_for_paths(source_path, module_path),
                        path=source_path or module_path or path,
                        source_path=source_path,
                        module_path=module_path,
                    )
                )
    return records


def _unique_records(namespace: str) -> dict[str, LibraryEntryRecord]:
    """Return one unique record per public name or fail on duplicate layouts."""
    by_name: dict[str, list[LibraryEntryRecord]] = {}
    for record in _candidate_records(namespace):
        _validate_public_entry_name(record.name, f"{namespace} library entry")
        by_name.setdefault(record.name, []).append(record)
    unique: dict[str, LibraryEntryRecord] = {}
    for name, records in by_name.items():
        if len(records) > 1:
            paths = ", ".join(str(r.path) for r in records)
            raise CompileError(f"Duplicate {namespace} library entry {name!r}: {paths}")
        unique[name] = records[0]
    return unique


def library_entry_records(namespace: str) -> list[dict[str, str]]:
    """Return discovered records for a catalog with unique public names."""
    return [record.as_dict() for record in sorted(_unique_records(namespace).values(), key=lambda r: r.name.lower())]


def library_entry_names(namespace: str) -> set[str]:
    """Return all callable public names from one catalog namespace."""
    return set(_unique_records(namespace).keys())


def find_library_entry_record(namespace: str, name: str) -> LibraryEntryRecord | None:
    """Return the unique source/native record for a public catalog name."""
    _validate_public_entry_name(name, f"{namespace} library entry")
    return _unique_records(namespace).get(name)


def has_library_entry(namespace: str, name: str) -> bool:
    """Return True if a catalog entry exists for *name*."""
    _catalog(namespace)
    if not _is_public_function_name(name):
        return False
    return find_library_entry_record(namespace, name) is not None


def load_library_entry_source(namespace: str, name: str) -> str:
    """Load editable NodeForge source code for a catalog entry."""
    record = find_library_entry_record(namespace, name)
    if record is None or record.source_path is None:
        raise CompileError(f"{namespace} library entry {name!r} has no editable .nf source")
    return record.source_path.read_text(encoding="utf-8")


def _module_path_for_entry(namespace: str, name: str) -> Path | None:
    """Find a namespace-specific trusted native helper module."""
    record = find_library_entry_record(namespace, name)
    if record is None:
        return None
    return record.module_path


def _load_entry_module(namespace: str, name: str):
    """Load a trusted native helper module for a catalog entry."""
    catalog = _catalog(namespace)
    path = _module_path_for_entry(namespace, name)
    if path is None or not catalog.allow_native or catalog.native_module_file is None:
        raise CompileError(f"Unknown native {namespace} library entry: {name}")
    package = __package__ or "NodeForge"
    module_stem = Path(catalog.native_module_file).stem
    module_name = f"{package}.{catalog.dirname}.{name}.{module_stem}"
    existing = sys.modules.get(module_name)
    if existing is not None and getattr(existing, "__file__", None) == str(path):
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise CompileError(f"Could not load {namespace} backend module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def has_module_library_entry(namespace: str, name: str) -> bool:
    """Return True if a trusted native module exists for a catalog entry."""
    return _module_path_for_entry(namespace, name) is not None


def has_native_compile_call(namespace: str, name: str | None = None) -> bool:
    """Return True when a trusted module owns whole-call compilation.

    The one-argument form is kept as a compatibility alias for functions.
    """
    if name is None:
        namespace, name = "functions", namespace
    if _module_path_for_entry(namespace, name) is None:
        return False
    module = _load_entry_module(namespace, name)
    return callable(getattr(module, "compile_call", None))


def backend_builtins_for_entry(namespace: str, name: str) -> dict[str, object]:
    """Return package-local backend helpers exposed while compiling source.nf."""
    if _module_path_for_entry(namespace, name) is None:
        return {}
    module = _load_entry_module(namespace, name)
    builtins = getattr(module, "BACKEND_BUILTINS", None)
    if builtins is None:
        return {}
    if not isinstance(builtins, dict):
        raise CompileError(f"{namespace} module {name} BACKEND_BUILTINS must be a dict")
    return dict(builtins)


def compile_module_library_entry_call(comp, expr, depth=0, namespace="functions", entry_name=None):
    """Compile a call handled by a trusted native helper module."""
    name = entry_name or expr.func.id
    module = _load_entry_module(namespace, name)
    compile_call = getattr(module, "compile_call", None)
    if compile_call is None:
        raise CompileError(f"{namespace} module {name} must define compile_call(comp, expr, depth=0)")
    return compile_call(comp, expr, depth)


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


def _record_kind(namespace: str, name: str) -> str:
    """Return a compact UI kind label for a discovered catalog entry."""
    record = find_library_entry_record(namespace, name)
    if record is None:
        return "script"
    return record.kind


def _backend_signature_for_record(record: LibraryEntryRecord | None) -> str:
    """Return the trusted backend signature for source recompilation checks."""
    if record is None or record.module_path is None:
        return ""
    return str(record.module_path.stat().st_mtime_ns)


def _assert_owned_materialized_group(existing, namespace: str, name: str) -> None:
    """Reject name-only reuse for new catalog-generated datablocks."""
    if namespace == "functions":
        return
    try:
        owned = existing.get("nodeforge_library_namespace") == namespace and existing.get("nodeforge_library_name") == name
    except Exception:
        owned = False
    if not owned:
        raise CompileError(f"{namespace} library group name collision: {getattr(existing, 'name', _group_name(namespace, name))}")


def get_or_create_library_entry_group(namespace: str, name: str, compile_group_callback):
    """Compile/update the node group that backs an editable catalog source."""
    record = find_library_entry_record(namespace, name)
    if record is None or record.source_path is None:
        raise CompileError(f"{namespace} library entry {name!r} has no editable .nf source")
    source = load_library_entry_source(namespace, name)
    backend_builtins = backend_builtins_for_entry(namespace, name)
    backend_signature = _backend_signature_for_record(record)
    group_name = _group_name(namespace, name)
    existing = bpy.data.node_groups.get(group_name)
    if existing is not None and getattr(existing, "bl_idname", None) == "GeometryNodeTree":
        _assert_owned_materialized_group(existing, namespace, name)
        try:
            if existing.get("nodeforge_library_source") == source and existing.get("nodeforge_backend_signature") == backend_signature:
                return existing
        except Exception:
            pass
        group = compile_group_callback(source, group_name, existing_group=existing, backend_builtins=backend_builtins)
    else:
        group = compile_group_callback(source, group_name, backend_builtins=backend_builtins)
    try:
        group["nodeforge_library_namespace"] = namespace
        group["nodeforge_library_name"] = name
        group["nodeforge_library_source"] = source
        group["nodeforge_function_kind"] = _record_kind(namespace, name)
        group["nodeforge_backend_signature"] = backend_signature
    except Exception:
        pass
    return group


def make_library_call_node(group, function_group, compiled_args, const_args, x=0, y=0):
    """Create a GeometryNodeGroup call to a compiled library node group."""
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


def materialize_library_entry_group(namespace: str, name: str, compile_group_callback):
    """Create/update a GeometryNodeTree for a catalog entry."""
    record = find_library_entry_record(namespace, name)
    if record is None:
        raise CompileError(f"Unknown {namespace} library entry: {name}")
    if record.module_path is not None:
        module = _load_entry_module(namespace, name)
        materialize = getattr(module, "materialize_group", None)
        if materialize is not None:
            group = materialize(compile_group_callback)
            try:
                group["nodeforge_library_namespace"] = namespace
                group["nodeforge_library_name"] = name
                group["nodeforge_function_kind"] = record.kind
                group["nodeforge_backend_signature"] = _backend_signature_for_record(record)
            except Exception:
                pass
            return apply_function_group_display_name(group, name) if namespace == "functions" else group
    if record.source_path is not None:
        group = get_or_create_library_entry_group(namespace, name, compile_group_callback)
        return apply_function_group_display_name(group, name) if namespace == "functions" else group
    raise CompileError(f"Unknown {namespace} library entry: {name}")


def ensure_local_catalog_dir() -> Path:
    """Create and return the user-owned local catalog directory."""
    path = catalog_dir("local")
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_library_entry_name(raw: str) -> str:
    """Validate a user-facing public script name for the local catalog."""
    name = (raw or "").strip()
    return _validate_public_entry_name(name, "Local script")


def sanitize_local_folder_path(raw: str) -> tuple[str, ...]:
    """Validate an optional slash-separated local folder path."""
    raw = (raw or "").strip().replace("\\", "/")
    if not raw:
        return ()
    p = Path(raw)
    if p.is_absolute() or re.match(r"^[A-Za-z]:", raw):
        raise CompileError("Local folder path must be relative")
    parts = tuple(part for part in raw.split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        raise CompileError("Local folder path contains an invalid segment")
    for part in parts:
        if not _is_public_function_name(part):
            raise CompileError("Local folder path segments must be valid public names")
    return parts


def create_local_folder(folder_path: str) -> Path:
    """Create a validated user-owned folder under local/."""
    parts = sanitize_local_folder_path(folder_path)
    root = ensure_local_catalog_dir()
    target = root.joinpath(*parts) if parts else root
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise CompileError("Local folder path escapes local catalog") from exc
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_local_source(name: str, source: str, *, folder_path: str = "", overwrite: bool = False) -> Path:
    """Persist a DSL source file inside local/ with duplicate-safe semantics."""
    public_name = sanitize_library_entry_name(name)
    if not (source or "").strip():
        raise CompileError("Local script source is empty")
    folder_parts = sanitize_local_folder_path(folder_path)
    root = ensure_local_catalog_dir()
    folder = root.joinpath(*folder_parts) if folder_parts else root
    try:
        folder.relative_to(root)
    except ValueError as exc:
        raise CompileError("Local save path escapes local catalog") from exc
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{public_name}.nf"

    existing = find_library_entry_record("local", public_name)
    if not overwrite:
        if existing is not None:
            raise CompileError(f"Local script {public_name!r} already exists")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(str(target), flags, 0o644)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(source)
        except Exception:
            try:
                target.unlink(missing_ok=True)
            finally:
                raise
        return target

    if existing is None:
        raise CompileError(f"Local script {public_name!r} does not exist")
    if existing.source_path != target or target.suffix != ".nf" or target.name != f"{public_name}.nf":
        raise CompileError(f"Local script {public_name!r} exists through a different layout or folder")
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{public_name}.", suffix=".tmp", dir=str(folder))
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(source)
        os.replace(str(tmp_path), str(target))
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    return target


# Compatibility wrappers for the existing functions catalog.
def _library_dir() -> Path:
    """Return the legacy function-library directory."""
    return catalog_dir("functions")


def _source_path_for_name(name: str) -> Path | None:
    """Find an editable functions-catalog source file."""
    record = find_library_entry_record("functions", name)
    return None if record is None else record.source_path


def _module_path_for_name(name: str) -> Path | None:
    """Find a packaged native functions-catalog module."""
    return _module_path_for_entry("functions", name)


def has_module_library_function(name: str) -> bool:
    """Return True if a native Python helper exists for a function."""
    return has_module_library_entry("functions", name)


def backend_builtins_for_function(name: str) -> dict[str, object]:
    """Return package-local backend helpers for a functions entry."""
    return backend_builtins_for_entry("functions", name)


def compile_module_library_function_call(comp, expr, depth=0, function_name=None):
    """Compile a functions-catalog native call."""
    return compile_module_library_entry_call(comp, expr, depth, namespace="functions", entry_name=function_name)


def library_function_names() -> set[str]:
    """Return all callable function names from the functions folder."""
    return library_entry_names("functions")


def has_library_function(name: str) -> bool:
    """Return True if a functions-catalog source or native helper exists."""
    return has_library_entry("functions", name)


def load_library_source(name: str) -> str:
    """Load editable NodeForge source code for a functions entry."""
    return load_library_entry_source("functions", name)


def get_or_create_library_group(name: str, compile_group_callback):
    """Compile/update a functions-catalog source group."""
    return get_or_create_library_entry_group("functions", name, compile_group_callback)


def library_function_records() -> list[dict[str, str]]:
    """Return discovered function records with name, kind and path."""
    return library_entry_records("functions")


def materialize_library_function_group(name: str, compile_group_callback):
    """Create/update a reusable GeometryNodeTree for a functions entry."""
    return materialize_library_entry_group("functions", name, compile_group_callback)


__all__ = [
    "CATALOGS",
    "LibraryCatalog",
    "LibraryEntryRecord",
    "catalog_dir",
    "library_entry_names",
    "library_entry_records",
    "find_library_entry_record",
    "has_library_entry",
    "load_library_entry_source",
    "materialize_library_entry_group",
    "get_or_create_library_entry_group",
    "make_library_call_node",
    "display_name_for_function",
    "display_name_for_group",
    "apply_function_node_display_name",
    "has_module_library_entry",
    "has_native_compile_call",
    "backend_builtins_for_entry",
    "compile_module_library_entry_call",
    "ensure_local_catalog_dir",
    "sanitize_library_entry_name",
    "sanitize_local_folder_path",
    "create_local_folder",
    "save_local_source",
    "library_function_names",
    "library_function_records",
    "materialize_library_function_group",
    "has_library_function",
    "load_library_source",
    "get_or_create_library_group",
    "has_module_library_function",
    "backend_builtins_for_function",
    "compile_module_library_function_call",
    "_normalized_socket_name",
    "_socket_type_to_value_type",
]
