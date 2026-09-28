"""Namespace-aware library discovery, local storage, and node-group calls."""

from __future__ import annotations

import errno
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Collection, Iterable, TYPE_CHECKING

import bpy

from .errors import CompileError
from .blender_group_authority import is_authority_ineligible_group
from .compiler_identities import CORE_PACKAGE_ID, FunctionId, GroupCompilationIdentity, library_function_id, normalize_library_package_id
from .semantic_ir import IRFunctionMaterialization
from .function_materializer import (
    FunctionMaterializationContext,
    FunctionMaterializer,
    MaterializedFunctionGroup,
    LibraryFunctionMaterializationSpec,
    LibraryFunctionUpdateSpec,
)
from .systems import registry as systems_registry
from . import packages
from .function_instances import (
    FUNCTION_DEFINITION_OWNER_PROP,
    FUNCTION_INSTANCE_KEY_PROP,
    direct_library_owner_scope,
)

if TYPE_CHECKING:
    from .resolved_environment import ResolvedCatalog

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
    interface_path: Path | None = None
    legacy_module_path: Path | None = None
    folder_path: str = ""
    package_id: str = ""
    package_name: str = ""
    package_version: str = ""

    def as_dict(self) -> dict[str, str]:
        """Return a UI/test-friendly dictionary representation."""
        return {
            "namespace": self.namespace,
            "name": self.name,
            "kind": self.kind,
            "path": str(self.path),
            "folder_path": self.folder_path,
            "package_id": self.package_id,
            "package_name": self.package_name,
            "package_version": self.package_version,
        }




_LOCAL_SOURCES_REGISTRY_VERSION = 1


def _local_sources_registry_path() -> Path:
    """Return the user-owned registry file for externally linked Local source folders."""
    root = Path(bpy.utils.user_resource("DATAFILES", path="nodeforge", create=True))
    root.mkdir(parents=True, exist_ok=True)
    return root / "local_sources.json"


def _canonical_path(path: Path | str) -> Path:
    """Return a normalized absolute filesystem path without requiring it to exist."""
    return Path(path).expanduser().resolve(strict=False)


def _path_key(path: Path | str) -> str:
    """Return a platform-normalized key used to compare configured source roots."""
    return os.path.normcase(str(_canonical_path(path)))


def _paths_overlap(first: Path | str, second: Path | str) -> bool:
    """Return True when two canonical directories are equal or one contains the other."""
    a = _canonical_path(first)
    b = _canonical_path(second)
    try:
        a.relative_to(b)
        return True
    except ValueError:
        pass
    try:
        b.relative_to(a)
        return True
    except ValueError:
        return False


def _read_local_source_registry() -> list[dict[str, str]]:
    """Load linked Local source roots from the user-owned registry."""
    path = _local_sources_registry_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise CompileError(f"Could not read Local source registry: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != _LOCAL_SOURCES_REGISTRY_VERSION:
        raise CompileError("Unsupported Local source registry format")
    roots = payload.get("roots", [])
    if not isinstance(roots, list):
        raise CompileError("Invalid Local source registry roots")
    result = []
    for item in roots:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise CompileError("Invalid Local source registry entry")
        kind = str(item.get("kind") or "folder")
        if kind not in {"folder", "file"}:
            raise CompileError("Invalid Local source registry entry kind")
        result.append({"path": item["path"], "label": str(item.get("label") or ""), "kind": kind})
    return result


def _write_local_source_registry(roots: list[dict[str, str]]) -> None:
    """Atomically persist linked Local source roots outside the installed add-on."""
    path = _local_sources_registry_path()
    payload = {"version": _LOCAL_SOURCES_REGISTRY_VERSION, "roots": roots}
    fd, temp_name = tempfile.mkstemp(prefix="local_sources.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _local_source_roots_from_registry(
    registry: Iterable[dict[str, str]],
    *,
    include_missing: bool = False,
) -> list[dict[str, object]]:
    """Derive Local folder roots from one already-read registry snapshot."""
    managed = _canonical_path(_default_local_catalog_dir())
    records: list[dict[str, object]] = [{
        "path": managed,
        "label": "Managed",
        "managed": True,
        "exists": managed.is_dir(),
    }]
    seen = {_path_key(managed)}
    for item in registry:
        if item.get("kind", "folder") != "folder":
            continue
        path = _canonical_path(item["path"])
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        exists = path.is_dir()
        if exists or include_missing:
            records.append({
                "path": path,
                "label": item.get("label") or path.name or str(path),
                "managed": False,
                "exists": exists,
            })
    return records


def local_source_roots(*, include_missing: bool = False) -> list[dict[str, object]]:
    """Return the managed Local root followed by configured external source roots."""
    return _local_source_roots_from_registry(
        _read_local_source_registry(),
        include_missing=include_missing,
    )


def link_local_source_folder(path: str, *, label: str = "") -> Path:
    """Register one non-overlapping external directory as a read-only Local root."""
    target = _canonical_path(path)
    if not target.is_dir():
        raise CompileError(f"Local source folder does not exist: {target}")
    managed = _canonical_path(_default_local_catalog_dir())
    if _paths_overlap(target, managed):
        raise CompileError("Imported Local folders may not overlap the managed Local catalog")

    roots = _read_local_source_registry()
    target_key = _path_key(target)
    for item in roots:
        if item.get("kind", "folder") != "folder":
            continue
        existing = _canonical_path(item["path"])
        if _path_key(existing) == target_key:
            return target
        if _paths_overlap(target, existing):
            raise CompileError("Imported Local folders may not overlap each other")

    roots.append({"path": str(target), "label": (label or target.name or str(target)).strip(), "kind": "folder"})
    _write_local_source_registry(roots)
    return target


def _local_source_files_from_registry(
    registry: Iterable[dict[str, str]],
    *,
    include_missing: bool = False,
) -> list[dict[str, object]]:
    """Derive linked Local files from one already-read registry snapshot."""
    records: list[dict[str, object]] = []
    for item in registry:
        if item.get("kind", "folder") != "file":
            continue
        path = _canonical_path(item["path"])
        exists = path.is_file()
        if exists or include_missing:
            records.append({
                "path": path,
                "label": item.get("label") or path.name or str(path),
                "managed": False,
                "exists": exists,
            })
    return records


def local_source_files(*, include_missing: bool = False) -> list[dict[str, object]]:
    """Return individually linked external Local source files."""
    return _local_source_files_from_registry(
        _read_local_source_registry(),
        include_missing=include_missing,
    )


def _unlink_local_source_registration(path: str | Path, *, kind: str) -> Path:
    """Remove one Local registry entry of *kind* without mutating its external path."""
    target = _canonical_path(path)
    target_key = _path_key(target)
    roots = _read_local_source_registry()
    kept = [
        item for item in roots
        if not (_path_key(item["path"]) == target_key and item.get("kind", "folder") == kind)
    ]
    if len(kept) == len(roots):
        noun = "folder" if kind == "folder" else "file"
        raise CompileError(f"Local source {noun} is not linked: {target}")
    _write_local_source_registry(kept)
    return target


def unlink_local_source_folder(path: str | Path) -> Path:
    """Remove one imported Local root registration without touching external files."""
    return _unlink_local_source_registration(path, kind="folder")


def unlink_local_source_file(path: str | Path) -> Path:
    """Remove one legacy linked-file registration without touching the external file."""
    return _unlink_local_source_registration(path, kind="file")


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


def _validate_public_entry_name(
    name: str,
    context: str = "Library entry",
    *,
    system_constructor_names: Collection[str] | None = None,
) -> str:
    """Return a validated public library entry name."""
    if not _is_public_function_name(name):
        raise CompileError(f"{context} name must be a valid public NodeForge import name")
    if system_constructor_names is None:
        systems_registry.validate_no_reserved_collision(name, context)
    elif name in system_constructor_names:
        raise CompileError(f"{context} {name!r} collides with reserved system constructor name")
    return name


def display_name_for_function(name: str) -> str:
    """Return the user-facing node title for a library entry name."""
    parts = [p for p in re.split(r"[_\s]+", name or "") if p]
    if not parts:
        return name or "Function"
    return "".join(part[:1].upper() + part[1:] for part in parts)


def display_name_for_group(group) -> str:
    """Return a clean title for a generated function node group."""
    try:
        local_name = group.get("nodeforge_local_function_name")
    except Exception:
        local_name = None
    if local_name:
        parts = [part for part in re.split(r"[_\s]+", str(local_name)) if part]
        return " ".join(part[:1].upper() + part[1:] for part in parts) or "Function"
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
    """Create the reusable group name for a function entry."""
    return display_name_for_function(name)


def _safe_package_component(value: str) -> str:
    """Return a stable datablock-name component for package-qualified groups."""
    return re.sub(r"[^A-Za-z0-9_]+", "_", value or "package").strip("_") or "package"


def _group_name(namespace: str, name: str) -> str:
    """Return the generated GeometryNodeTree name for an unowned catalog entry."""
    if namespace == "functions":
        return _safe_group_name(name)
    if namespace == "examples":
        return f"NodeForge.example.{name}"
    if namespace == "local":
        return f"NodeForge.local.{name}"
    return f"NodeForge.{namespace}.{name}"


def _group_name_for_record(record: "LibraryEntryRecord") -> str:
    """Return the package-aware GeometryNodeTree base name for a catalog record."""
    if record.namespace == "local":
        return _group_name("local", record.name)
    if not record.package_id:
        return _group_name(record.namespace, record.name)
    package_part = _safe_package_component(record.package_id)
    return f"NodeForge.package.{package_part}.{record.namespace}.{record.name}"


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


def _immediate_interface_path(root: Path, name: str) -> Path | None:
    """Return the v2 interface marker for one immediate package library owner."""
    path = root / name / "interface.py"
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


def _candidate_records_from_inputs(
    namespace: str,
    *,
    package_roots: Iterable[packages.LibraryRoot],
    local_registry: Iterable[dict[str, str]] = (),
    include_builtin_catalog: bool = True,
) -> list[LibraryEntryRecord]:
    """Return raw records from explicit package roots and one Local registry snapshot."""
    catalog = _catalog(namespace)
    roots: list[tuple[Path, str, str, str]] = []
    local_files: list[dict[str, object]] = []
    if namespace == "local":
        ensure_local_catalog_dir()
        registry = tuple(local_registry)
        roots.extend(
            (record["path"], "", "", "")
            for record in _local_source_roots_from_registry(registry)
        )
        local_files = _local_source_files_from_registry(registry)
    else:
        if namespace == "examples" and include_builtin_catalog:
            roots.append((catalog_dir(namespace), "", "", ""))
        roots.extend(
            (root.path, root.package_id, root.package_name, root.package_version)
            for root in package_roots
        )
    records: list[LibraryEntryRecord] = []
    for root, package_id, package_name, package_version in roots:
        if not root.exists():
            continue
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
            continue

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
                            package_id=package_id,
                            package_name=package_name,
                            package_version=package_version,
                        )
                    )
            elif path.is_dir() and _is_public_function_name(path.name):
                source_path = _immediate_source_path(root, path.name)
                interface_path = _immediate_interface_path(root, path.name) if catalog.allow_native else None
                legacy_module_path = _immediate_module_path(
                    root, path.name, catalog.native_module_file if catalog.allow_native else None
                )
                if interface_path is not None:
                    if source_path is not None:
                        # TODO(nodeforge-migration): Source-backed library owners with interface.py are reserved for the
                        # later hybrid-extension migration. This backend-only platform supports pure source entries and
                        # native-only v2 extension owners only; do not construct an owner-local helper view here. Remove
                        # this marker when hybrid source/interface execution and its resource-mutation contract are implemented.
                        kind = "v2_hybrid"
                    else:
                        kind = "extension"
                    records.append(
                        LibraryEntryRecord(
                            namespace=namespace,
                            name=path.name,
                            kind=kind,
                            path=source_path or interface_path,
                            source_path=source_path,
                            module_path=None,
                            interface_path=interface_path,
                            legacy_module_path=legacy_module_path,
                            package_id=package_id,
                            package_name=package_name,
                            package_version=package_version,
                        )
                    )
                elif source_path is not None or legacy_module_path is not None:
                    records.append(
                        LibraryEntryRecord(
                            namespace=namespace,
                            name=path.name,
                            kind=_record_kind_for_paths(source_path, legacy_module_path),
                            path=source_path or legacy_module_path or path,
                            source_path=source_path,
                            module_path=legacy_module_path,
                            interface_path=None,
                            legacy_module_path=legacy_module_path,
                            package_id=package_id,
                            package_name=package_name,
                            package_version=package_version,
                        )
                    )
    if namespace == "local":
        for linked in local_files:
            path = linked["path"]
            if path.suffix in _SOURCE_EXTENSIONS and _is_public_function_name(path.stem):
                records.append(LibraryEntryRecord(
                    namespace="local",
                    name=path.stem,
                    kind="script",
                    path=path,
                    source_path=path,
                    folder_path="",
                ))
    return records


def _candidate_records(namespace: str) -> list[LibraryEntryRecord]:
    """Return live raw records before duplicate-name reduction."""
    local_registry = tuple(_read_local_source_registry()) if namespace == "local" else ()
    return _candidate_records_from_inputs(
        namespace,
        package_roots=() if namespace == "local" else packages.library_roots(namespace),
        local_registry=local_registry,
    )

def local_browser_records(current_path: str = "") -> list[dict[str, object]]:
    """Return direct children for the Local mini file browser's current directory."""
    managed_root = _canonical_path(ensure_local_catalog_dir())
    linked_folders = local_source_roots(include_missing=True)[1:]
    linked_files = local_source_files(include_missing=True)

    location = _canonical_path(current_path) if current_path else None
    if location is None:
        directory = managed_root
        managed = True
        source_root = managed_root
        source_label = "Local"
    else:
        allowed = None
        try:
            location.relative_to(managed_root)
            allowed = (managed_root, True, "Local")
        except ValueError:
            for entry in linked_folders:
                root = _canonical_path(entry["path"])
                try:
                    location.relative_to(root)
                    allowed = (root, False, str(entry["label"]))
                    break
                except ValueError:
                    continue
        if allowed is None or not location.is_dir():
            directory = managed_root
            managed = True
            source_root = managed_root
            source_label = "Local"
            location = None
        else:
            source_root, managed, source_label = allowed
            directory = location

    script_by_path = {_path_key(record.path): record for record in _candidate_records("local")}
    rows: list[dict[str, object]] = []
    for path in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if path.name.startswith("_") or path.name.startswith("."):
            continue
        if path.is_dir():
            try:
                rel = path.relative_to(source_root)
            except ValueError:
                rel = Path(path.name)
            rows.append({
                "name": path.name,
                "kind": "folder",
                "path": str(path),
                "folder_path": str(rel).replace(os.sep, "/"),
                "root_path": str(source_root),
                "source_label": source_label,
                "managed": managed,
            })
            continue
        record = script_by_path.get(_path_key(path))
        if record is None:
            continue
        rows.append({
            **record.as_dict(),
            "root_path": str(source_root),
            "source_label": source_label,
            "managed": managed,
        })

    if location is None:
        for entry in linked_folders:
            path = _canonical_path(entry["path"])
            rows.append({
                "name": str(entry["label"]),
                "kind": "linked_folder" if entry["exists"] else "missing_linked_folder",
                "path": str(path),
                "folder_path": "",
                "root_path": str(path),
                "source_label": str(entry["label"]),
                "managed": False,
            })
        for entry in linked_files:
            path = _canonical_path(entry["path"])
            record = script_by_path.get(_path_key(path))
            if record is not None:
                rows.append({
                    **record.as_dict(),
                    "kind": "linked_script",
                    "root_path": str(path.parent),
                    "source_label": "",
                    "managed": False,
                })
            elif not entry["exists"]:
                rows.append({
                    "name": path.name,
                    "kind": "missing_linked_script",
                    "path": str(path),
                    "folder_path": "",
                    "root_path": str(path.parent),
                    "source_label": "",
                    "managed": False,
                })
    return rows


def _unique_records_from_candidates(
    namespace: str,
    candidates: Iterable[LibraryEntryRecord],
    *,
    system_constructor_names: Collection[str],
) -> dict[str, LibraryEntryRecord]:
    """Select one record per public name from explicit discovery candidates."""
    by_name: dict[str, list[LibraryEntryRecord]] = {}
    for record in candidates:
        _validate_public_entry_name(
            record.name,
            f"{namespace} library entry",
            system_constructor_names=system_constructor_names,
        )
        by_name.setdefault(record.name, []).append(record)
    unique: dict[str, LibraryEntryRecord] = {}
    for name, records in by_name.items():
        if len(records) > 1:
            paths = ", ".join(str(r.path) for r in records)
            raise CompileError(f"Duplicate {namespace} library entry {name!r}: {paths}")
        unique[name] = records[0]
    return unique


def resolve_catalog(
    namespace: str,
    *,
    package_roots: Iterable[packages.LibraryRoot],
    system_constructor_names: Collection[str],
) -> "ResolvedCatalog":
    """Resolve one catalog from explicit package/system inputs as success or failure."""
    from .resolved_environment import (
        ResolvedCatalog,
        ResolvedCompileErrorFailure,
        ResolvedOSErrorFailure,
    )

    try:
        local_registry = tuple(_read_local_source_registry()) if namespace == "local" else ()
        candidates = _candidate_records_from_inputs(
            namespace,
            package_roots=tuple(package_roots),
            local_registry=local_registry,
        )
        entries = _unique_records_from_candidates(
            namespace,
            candidates,
            system_constructor_names=system_constructor_names,
        )
    except CompileError as exc:
        failure = ResolvedCompileErrorFailure(tuple(exc.args))
    except OSError as exc:
        failure = ResolvedOSErrorFailure(
            exception_type=type(exc),
            exception_args=tuple(exc.args),
            errno=exc.errno,
            strerror=exc.strerror,
            filename=exc.filename,
            filename2=exc.filename2,
            winerror=getattr(exc, "winerror", None),
        )
    else:
        return ResolvedCatalog(namespace, entries)
    return ResolvedCatalog(namespace, {}, failure)


def _unique_records(namespace: str) -> dict[str, LibraryEntryRecord]:
    """Return one live unique record per public name or replay discovery failure."""
    catalog = resolve_catalog(
        namespace,
        package_roots=() if namespace == "local" else packages.library_roots(namespace),
        system_constructor_names=systems_registry.constructor_names(),
    )
    return {record.name: record for record in catalog.records()}


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
    if record is None:
        raise CompileError(f"{namespace} library entry {name!r} has no editable .nf source")
    return load_library_entry_source_for_record(record)


def _validate_resolved_record(record: LibraryEntryRecord) -> None:
    """Validate the intrinsic namespace/name/path contract of a selected record."""
    _catalog(record.namespace)
    if not _is_public_function_name(record.name):
        raise CompileError(f"{record.namespace} library entry name must be a valid public NodeForge import name")
    if record.path != (record.source_path or record.module_path or record.path):
        raise CompileError("Internal error: resolved library record path is inconsistent")


def load_library_entry_source_for_record(record: LibraryEntryRecord) -> str:
    """Load editable source from an exact resolved catalog record."""
    _validate_resolved_record(record)
    if record.source_path is None:
        raise CompileError(f"{record.namespace} library entry {record.name!r} has no editable .nf source")
    return record.source_path.read_text(encoding="utf-8")


def _module_path_for_entry(namespace: str, name: str) -> Path | None:
    """Find a namespace-specific trusted native helper module."""
    record = find_library_entry_record(namespace, name)
    if record is None:
        return None
    return record.module_path


def has_module_library_entry(namespace: str, name: str) -> bool:
    """Return whether discovery found a legacy native Python library owner.

    This is structural characterization only. Stage 35 never imports or executes
    the native module; supported Python extensions use ``interface.py`` through
    Extension API v2.
    """
    return _module_path_for_entry(namespace, name) is not None


def _input_sockets(node):
    """Return real, non-hidden input sockets for a group node."""
    return [s for s in node.inputs if not getattr(s, "is_output", False) and getattr(s, "enabled", True) and not getattr(s, "hide", False)]


def _output_sockets(node):
    """Return real, non-hidden output sockets for a group node."""
    return [s for s in node.outputs if getattr(s, "is_output", True) and getattr(s, "enabled", True) and not getattr(s, "hide", False)]


def _record_kind(namespace: str, name: str) -> str:
    """Return a compact UI kind label for a discovered catalog entry."""
    record = find_library_entry_record(namespace, name)
    if record is None:
        return "script"
    return record.kind


def _group_catalog_provenance(group) -> tuple[str, str]:
    """Return authoritative catalog namespace/name stored on a materialized group."""
    if is_authority_ineligible_group(group):
        raise CompileError("Selected node group is transaction-private or was not published")
    try:
        namespace = str(group.get("nodeforge_library_namespace") or "")
        name = str(group.get("nodeforge_library_name") or "")
    except Exception as exc:
        raise CompileError("Selected node group has no readable NodeForge library provenance") from exc
    if not namespace or not name:
        raise CompileError("Selected node group has no NodeForge library provenance")
    return namespace, name


def resolve_reloadable_library_entry(group, namespace: str | None = None, name: str | None = None) -> LibraryEntryRecord:
    """Resolve the current editable catalog record compatible with an existing group.

    Package version is intentionally not stable ownership for reloads: a group
    may move to a newer installed version of the same package. Package identity
    itself remains stable so an unrelated package cannot claim the datablock.
    """
    stored_namespace, stored_name = _group_catalog_provenance(group)
    namespace = stored_namespace if namespace is None else namespace
    name = stored_name if name is None else name
    if namespace != stored_namespace or name != stored_name:
        raise CompileError(
            f"Selected node group belongs to {stored_namespace}/{stored_name}, not {namespace}/{name}"
        )
    record = find_library_entry_record(namespace, name)
    if record is None:
        raise CompileError(f"Current source for {namespace} library entry {name!r} is unavailable")
    return validate_reloadable_library_entry_record(group, record)


def validate_reloadable_library_entry_record(group, record: LibraryEntryRecord) -> LibraryEntryRecord:
    """Validate an exact selected record against an existing group's provenance."""
    _validate_resolved_record(record)
    stored_namespace, stored_name = _group_catalog_provenance(group)
    if record.namespace != stored_namespace or record.name != stored_name:
        raise CompileError(
            f"Selected node group belongs to {stored_namespace}/{stored_name}, not {record.namespace}/{record.name}"
        )
    if record.source_path is None:
        raise CompileError(f"{record.namespace} library entry {record.name!r} has no reloadable .nf source")

    try:
        stored_package_id = str(group.get("nodeforge_package_id") or CORE_PACKAGE_ID)
    except Exception:
        stored_package_id = CORE_PACKAGE_ID
    current_package_id = normalize_library_package_id(record.package_id)
    if stored_package_id != current_package_id:
        raise CompileError(
            f"Current source for {record.namespace} library entry {record.name!r} belongs to a different package"
        )
    return record


def update_materialized_library_entry_group(namespace: str, name: str, group, group_backend):
    """Recompile the current editable catalog source into an existing root group."""
    record = resolve_reloadable_library_entry(group, namespace, name)
    return update_materialized_library_entry_group_for_record(record, group, group_backend)


def update_materialized_library_entry_group_for_record(record: LibraryEntryRecord, group, group_backend):
    """Update an existing root group through one prepared semantic compilation."""
    validate_reloadable_library_entry_record(group, record)
    source = load_library_entry_source_for_record(record)
    function_id = library_function_id(record.namespace, record.package_id, record.name)
    identity = _direct_library_compilation_identity(function_id)
    session = group_backend.new_source_callable_session()
    prepared = group_backend.prepare_source_compilation(
        source,
        compilation_identity=identity,
        helper_namespace=record.name,
        source_callable_session=session,
    )
    spec = LibraryFunctionUpdateSpec(
        namespace=record.namespace,
        name=record.name,
        record=record,
        prepared_compilation=prepared,
        function_id=function_id,
        source_callable_session=session,
        group=group,
        group_name=getattr(group, "name", _group_name_for_record(record)),
        write_package_metadata=_write_package_metadata,
    )
    return FunctionMaterializer(group_backend=group_backend).update_library_group(spec)


def _assert_owned_materialized_group(existing, record: LibraryEntryRecord, group_name: str) -> None:
    """Reject cross-package reuse for catalog-generated datablocks."""
    try:
        owned = existing.get("nodeforge_library_namespace") == record.namespace and existing.get("nodeforge_library_name") == record.name
        if record.package_id:
            owned = (
                owned
                and existing.get("nodeforge_package_id") == record.package_id
                and existing.get("nodeforge_package_version") == record.package_version
            )
    except Exception:
        owned = False
    if not owned:
        raise CompileError(f"{record.namespace} library group name collision: {getattr(existing, 'name', group_name)}")


def _write_package_metadata(group, record: LibraryEntryRecord) -> None:
    """Record package ownership metadata on materialized library groups."""
    group["nodeforge_library_namespace"] = record.namespace
    group["nodeforge_library_name"] = record.name
    group["nodeforge_function_kind"] = record.kind
    group["nodeforge_package_id"] = record.package_id or CORE_PACKAGE_ID
    if record.package_id:
        group["nodeforge_package_name"] = record.package_name
        group["nodeforge_package_version"] = record.package_version


def _library_metadata_matches(group, record: LibraryEntryRecord, *, instance_key: str | None) -> bool:
    """Return True when *group* is the exact metadata owner for *record*."""
    try:
        if group.get("nodeforge_library_namespace") != record.namespace:
            return False
        if group.get("nodeforge_library_name") != record.name:
            return False
        stored_package_id = str(group.get("nodeforge_package_id") or CORE_PACKAGE_ID)
        if stored_package_id != normalize_library_package_id(record.package_id):
            return False
        stored_key = str(group.get(FUNCTION_INSTANCE_KEY_PROP) or "")
        wanted_key = str(instance_key or "")
        return stored_key == wanted_key
    except Exception:
        return False


def _find_owned_library_entry_group(record: LibraryEntryRecord, *, instance_key: str | None = None, transaction=None):
    """Find the sole live imported function group by ownership metadata."""
    matches = []
    for group in bpy.data.node_groups:
        if getattr(group, "bl_idname", None) != "GeometryNodeTree":
            continue
        if transaction is not None and hasattr(transaction, "owns_group") and transaction.owns_group(group):
            continue
        if is_authority_ineligible_group(group):
            continue
        if _library_metadata_matches(group, record, instance_key=instance_key):
            matches.append(group)
    if len(matches) > 1:
        mode = "unique" if instance_key else "shared"
        raise CompileError(f"Multiple {mode} {record.namespace} library groups match {record.name!r}")
    return matches[0] if matches else None


def _validate_library_function_id(function_id: FunctionId, record: LibraryEntryRecord) -> None:
    """Validate one upstream canonical imported identity against a resolved record."""
    if (
        function_id.kind != "LIBRARY"
        or function_id.namespace != record.namespace
        or function_id.package_id != normalize_library_package_id(record.package_id)
        or function_id.name != record.name
    ):
        raise CompileError(
            f"Internal error: reusable-call FunctionId does not match {record.namespace} library entry {record.name!r}"
        )


def _direct_library_compilation_identity(function_id: FunctionId) -> GroupCompilationIdentity:
    """Return the established direct-catalog owner before semantic preparation."""
    owner_scope = direct_library_owner_scope(
        function_id.namespace,
        function_id.package_id,
        function_id.name,
    )
    definition_owner = owner_scope if function_id.namespace == "local" else function_id.stable_key()
    return GroupCompilationIdentity(
        root_owner_id=None,
        owner_scope=owner_scope,
        definition_owner=definition_owner,
        declaration_owner=function_id.stable_key(),
    )


def materialize_prepared_library_callable(
    record: LibraryEntryRecord,
    materializer: FunctionMaterializer,
    prepared_callable,
    *,
    materialization: IRFunctionMaterialization | None,
    materialization_context: FunctionMaterializationContext,
) -> MaterializedFunctionGroup:
    """Materialize one source-call callee from its captured prepared semantic artifact."""
    _validate_resolved_record(record)
    function_id = prepared_callable.contract.function_id
    _validate_library_function_id(function_id, record)
    if record.namespace == "local":
        if materialization is not None:
            raise CompileError("Internal error: Local catalog source call cannot carry reusable materialization")
    else:
        if materialization is None or materialization.callee != function_id:
            raise CompileError("Internal error: reusable source call has inconsistent materialization identity")
    spec = LibraryFunctionMaterializationSpec(
        namespace=record.namespace,
        name=record.name,
        record=record,
        prepared_compilation=prepared_callable.group,
        function_id=function_id,
        source_callable_session=materialization_context.source_callable_session,
        materialization=materialization,
        group_name=_group_name_for_record(record),
        find_existing=_find_owned_library_entry_group,
        write_package_metadata=_write_package_metadata,
    )
    return materializer.materialize_library(spec, context=materialization_context)


def get_or_create_library_entry_group(
    namespace: str,
    name: str,
    group_backend,
    *,
    materialization: IRFunctionMaterialization | None = None,
    function_id: FunctionId | None = None,
    materialization_context: FunctionMaterializationContext | None = None,
) -> MaterializedFunctionGroup:
    """Resolve an editable catalog definition and return its materialization result."""
    record = find_library_entry_record(namespace, name)
    if record is None:
        raise CompileError(f"{namespace} library entry {name!r} has no editable .nf source")
    return get_or_create_library_entry_group_for_record(
        record,
        group_backend,
        materialization=materialization,
        function_id=function_id,
        materialization_context=materialization_context,
    )


def get_or_create_library_entry_group_for_record(
    record: LibraryEntryRecord,
    group_backend,
    *,
    materialization: IRFunctionMaterialization | None = None,
    function_id: FunctionId | None = None,
    materialization_context: FunctionMaterializationContext | None = None,
    prepared_callable=None,
) -> MaterializedFunctionGroup:
    """Materialize an editable source definition from prepared semantics."""
    _validate_resolved_record(record)
    namespace = record.namespace
    name = record.name
    if record.source_path is None:
        raise CompileError(f"{namespace} library entry {name!r} has no editable .nf source")

    if prepared_callable is not None:
        if materialization_context is None:
            raise CompileError("Internal error: prepared source call requires materialization context")
        return materialize_prepared_library_callable(
            record,
            group_backend,
            prepared_callable,
            materialization=materialization,
            materialization_context=materialization_context,
        )

    if materialization is not None or function_id is not None:
        raise CompileError("Internal error: source-call materialization must provide PreparedSourceCallable")
    function_id = library_function_id(namespace, record.package_id, name)
    source = load_library_entry_source_for_record(record)
    identity = _direct_library_compilation_identity(function_id)
    session = group_backend.new_source_callable_session()
    prepared = group_backend.prepare_source_compilation(
        source,
        compilation_identity=identity,
        helper_namespace=name,
        source_callable_session=session,
    )
    spec = LibraryFunctionMaterializationSpec(
        namespace=namespace,
        name=name,
        record=record,
        prepared_compilation=prepared,
        function_id=function_id,
        source_callable_session=session,
        materialization=None,
        group_name=_group_name_for_record(record),
        find_existing=_find_owned_library_entry_group,
        write_package_metadata=_write_package_metadata,
    )
    return FunctionMaterializer(group_backend=group_backend).materialize_library(
        spec, context=materialization_context
    )


def materialize_library_entry_group(namespace: str, name: str, group_backend):
    """Create/update a GeometryNodeTree for a catalog entry."""
    record = find_library_entry_record(namespace, name)
    if record is None:
        raise CompileError(f"Unknown {namespace} library entry: {name}")
    return materialize_library_entry_group_for_record(record, group_backend)


def materialize_library_entry_group_for_record(record: LibraryEntryRecord, group_backend):
    """Create/update a GeometryNodeTree from one exact resolved catalog record."""
    _validate_resolved_record(record)
    namespace = record.namespace
    name = record.name
    if record.module_path is not None:
        raise CompileError(
            f"{namespace} library entry {name!r} uses unsupported Extension API v1 native execution; "
            "migrate the owner to interface.py (EXTENSION_API = 2)"
        )
    if record.source_path is not None:
        materialized = get_or_create_library_entry_group_for_record(record, group_backend)
        group = materialized.group
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


def _resolve_owned_local_path(path: Path | str, *, root: Path | None = None) -> Path:
    """Resolve *path* and require its current filesystem topology to stay in managed Local."""
    managed = _canonical_path(root or ensure_local_catalog_dir())
    resolved = _canonical_path(path)
    try:
        resolved.relative_to(managed)
    except ValueError as exc:
        raise CompileError("Local path resolves outside the managed Local catalog") from exc
    return resolved


def _managed_local_target(path: Path | str) -> Path:
    """Return a concrete managed path after checking its resolved ownership."""
    raw = Path(path).expanduser()
    root = _canonical_path(ensure_local_catalog_dir())
    candidate = raw if raw.is_absolute() else root / raw
    candidate = candidate.absolute()
    _resolve_owned_local_path(candidate, root=root)
    return candidate


def create_local_folder(folder_path: str) -> Path:
    """Create a validated folder under the managed Local root without following escapes."""
    parts = sanitize_local_folder_path(folder_path)
    if not parts:
        raise CompileError("Local folder path is empty")
    root = _canonical_path(ensure_local_catalog_dir())
    current = root
    for part in parts:
        candidate = current / part
        resolved = _resolve_owned_local_path(candidate, root=root)
        if candidate.exists() or candidate.is_symlink():
            if not resolved.is_dir():
                raise CompileError(f"Local folder path is not a directory: {candidate}")
            current = resolved
            continue
        try:
            candidate.mkdir()
        except OSError as exc:
            raise CompileError(f"Could not create Local folder {candidate.name!r}: {exc}") from exc
        current = _resolve_owned_local_path(candidate, root=root)
    return current


def delete_local_source(path: str | Path) -> Path:
    """Delete one concrete managed Local source file selected by filesystem path."""
    target = _managed_local_target(path)
    if target.suffix.lower() not in _SOURCE_EXTENSIONS:
        raise CompileError(f"Local source is not a deletable script: {target}")
    if not target.exists():
        raise CompileError(f"Local source does not exist: {target}")
    if not target.is_file():
        raise CompileError(f"Local source is not a regular file: {target}")
    try:
        target.unlink()
    except OSError as exc:
        raise CompileError(f"Could not delete Local source {target.name!r}: {exc}") from exc
    return target


def delete_local_folder(folder_path: str | Path) -> Path:
    """Delete one empty managed Local directory without recursive traversal."""
    root = _canonical_path(ensure_local_catalog_dir())
    target = _managed_local_target(folder_path)
    if target == root:
        raise CompileError("The managed Local root cannot be deleted")
    if not target.exists():
        raise CompileError(f"Local folder does not exist: {target}")
    if not target.is_dir():
        raise CompileError(f"Local folder is not a directory: {target}")
    try:
        target.rmdir()
    except OSError as exc:
        if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
            raise CompileError(f"Local folder is not empty: {target.name}") from exc
        raise CompileError(f"Could not delete Local folder {target.name!r}: {exc}") from exc
    return target


def save_local_source(name: str, source: str, *, folder_path: str = "", overwrite: bool = False) -> Path:
    """Persist one DSL source at an exact managed path without global name resolution."""
    public_name = sanitize_library_entry_name(name)
    if not (source or "").strip():
        raise CompileError("Local script source is empty")
    folder_parts = sanitize_local_folder_path(folder_path)
    root = _canonical_path(ensure_local_catalog_dir())
    folder = create_local_folder("/".join(folder_parts)) if folder_parts else root
    folder = _resolve_owned_local_path(folder, root=root)
    target_raw = folder / f"{public_name}.nf"
    target = _resolve_owned_local_path(target_raw, root=root)

    if not overwrite:
        if target_raw.exists() or target_raw.is_symlink():
            raise CompileError(f"Local script already exists at {target_raw}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(str(target_raw), flags, 0o644)
        except FileExistsError as exc:
            raise CompileError(f"Local script already exists at {target_raw}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(source)
        except Exception:
            try:
                target_raw.unlink(missing_ok=True)
            finally:
                raise
        return _resolve_owned_local_path(target_raw, root=root)

    if not target_raw.exists() or not target_raw.is_file():
        raise CompileError(f"Local script does not exist at {target_raw}")
    target = _resolve_owned_local_path(target_raw, root=root)
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{public_name}.", suffix=".tmp", dir=str(folder))
        tmp_path = Path(tmp_name)
        _resolve_owned_local_path(tmp_path, root=root)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(source)
        _resolve_owned_local_path(target_raw, root=root)
        os.replace(str(tmp_path), str(target_raw))
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


def library_function_names() -> set[str]:
    """Return all callable function names from the functions folder."""
    return library_entry_names("functions")


def has_library_function(name: str) -> bool:
    """Return True if a functions-catalog source or native helper exists."""
    return has_library_entry("functions", name)


def load_library_source(name: str) -> str:
    """Load editable NodeForge source code for a functions entry."""
    return load_library_entry_source("functions", name)


def get_or_create_library_group(name: str, group_backend):
    """Compile/update a functions-catalog source group."""
    return get_or_create_library_entry_group("functions", name, group_backend).group


def library_function_records() -> list[dict[str, str]]:
    """Return discovered function records with name, kind and path."""
    return library_entry_records("functions")


def materialize_library_function_group(name: str, group_backend):
    """Create/update a reusable GeometryNodeTree for a functions entry."""
    return materialize_library_entry_group("functions", name, group_backend)


__all__ = [
    "CATALOGS",
    "LibraryCatalog",
    "LibraryEntryRecord",
    "catalog_dir",
    "resolve_catalog",
    "library_entry_names",
    "library_entry_records",
    "find_library_entry_record",
    "has_library_entry",
    "load_library_entry_source",
    "load_library_entry_source_for_record",
    "resolve_reloadable_library_entry",
    "validate_reloadable_library_entry_record",
    "update_materialized_library_entry_group",
    "update_materialized_library_entry_group_for_record",
    "materialize_library_entry_group",
    "materialize_library_entry_group_for_record",
    "get_or_create_library_entry_group",
    "get_or_create_library_entry_group_for_record",
    "materialize_prepared_library_callable",
    "display_name_for_function",
    "display_name_for_group",
    "apply_function_node_display_name",
    "has_module_library_entry",
    "ensure_local_catalog_dir",
    "local_source_roots",
    "link_local_source_folder",
    "unlink_local_source_folder",
    "local_browser_records",
    "sanitize_library_entry_name",
    "sanitize_local_folder_path",
    "create_local_folder",
    "delete_local_folder",
    "delete_local_source",
    "save_local_source",
    "library_function_names",
    "library_function_records",
    "materialize_library_function_group",
    "has_library_function",
    "load_library_source",
    "get_or_create_library_group",
    "has_module_library_function",
]
