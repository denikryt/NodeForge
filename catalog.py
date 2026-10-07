"""Blender-independent catalog records and source discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .errors import CompileError
from . import packages


_SOURCE_EXTENSIONS = (".nf", ".nodeforge")
_PACKAGE_SOURCE_NAME = "source.nf"


@dataclass(frozen=True)
class LibraryCatalog:
    """Description of one public NodeForge library import namespace."""

    namespace: str
    dirname: str
    allow_native: bool
    native_module_file: str | None


CATALOGS = {
    "functions": LibraryCatalog("functions", "functions", True, "function.py"),
    "examples": LibraryCatalog("examples", "examples", True, "backend.py"),
    "local": LibraryCatalog("local", "local", False, None),
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


def _package_root() -> Path:
    """Return the NodeForge package directory."""
    return Path(__file__).resolve().parent


def _catalog(namespace: str) -> LibraryCatalog:
    """Return a known catalog descriptor or raise a controlled compile error."""
    try:
        return CATALOGS[namespace]
    except KeyError as exc:
        raise CompileError(f"Unknown library catalog: {namespace}") from exc


def catalog_dir(namespace: str) -> Path:
    """Return a package-owned catalog root; Local persistence is owned elsewhere."""
    catalog = _catalog(namespace)
    if catalog.namespace == "local":
        raise CompileError("Local catalog root is owned by local source storage")
    return _package_root() / catalog.dirname


def _is_valid_function_name(name: str) -> bool:
    """Return True when a file/directory stem can be called as a Python-like function."""
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""))


def _is_public_function_name(name: str) -> bool:
    """Return True for callable names exposed to user imports."""
    return _is_valid_function_name(name) and not name.startswith("_")


def _validate_public_entry_name(name: str, context: str = "Library entry") -> str:
    """Return a validated public library entry name without global callable reservation."""
    if not _is_public_function_name(name):
        raise CompileError(f"{context} name must be a valid public NodeForge import name")
    return name


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


def _path_value(item: Path | str | dict[str, object]) -> Path:
    """Normalize one pre-resolved path input without discovering external state."""
    if isinstance(item, dict):
        item = item["path"]
    return Path(item)


def candidate_records_from_inputs(
    namespace: str,
    *,
    package_roots: Iterable[packages.LibraryRoot],
    local_roots: Iterable[Path | str | dict[str, object]] = (),
    local_files: Iterable[Path | str | dict[str, object]] = (),
    include_builtin_catalog: bool = True,
) -> list[LibraryEntryRecord]:
    """Return raw catalog records from explicit roots/files supplied by the caller."""
    catalog = _catalog(namespace)
    roots: list[tuple[Path, str, str, str]] = []
    if namespace == "local":
        roots.extend((_path_value(root), "", "", "") for root in local_roots)
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
                    # A single owner containing both source.nf and interface.py is intentionally unsupported.
                    # Keep it explicitly classified so resolution can produce the dedicated mixed-owner diagnostic
                    # instead of silently choosing source execution or Extension API v2 execution.
                    kind = "v2_hybrid" if source_path is not None else "extension"
                    records.append(
                        LibraryEntryRecord(
                            namespace=namespace,
                            name=path.name,
                            kind=kind,
                            path=source_path or interface_path,
                            source_path=source_path,
                            module_path=None,
                            interface_path=interface_path,
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
                            package_id=package_id,
                            package_name=package_name,
                            package_version=package_version,
                        )
                    )

    if namespace == "local":
        for linked in local_files:
            path = _path_value(linked)
            if path.suffix in _SOURCE_EXTENSIONS and _is_public_function_name(path.stem):
                records.append(
                    LibraryEntryRecord(
                        namespace="local",
                        name=path.stem,
                        kind="script",
                        path=path,
                        source_path=path,
                        folder_path="",
                    )
                )
    return records


def unique_records_from_candidates(
    namespace: str,
    candidates: Iterable[LibraryEntryRecord],
) -> dict[str, LibraryEntryRecord]:
    """Select one record per public name for catalogs that remain globally unique."""
    by_name: dict[str, list[LibraryEntryRecord]] = {}
    for record in candidates:
        _validate_public_entry_name(record.name, f"{namespace} library entry")
        by_name.setdefault(record.name, []).append(record)
    unique: dict[str, LibraryEntryRecord] = {}
    for name, records in by_name.items():
        if len(records) > 1:
            paths = ", ".join(str(record.path) for record in records)
            raise CompileError(f"Duplicate {namespace} library entry {name!r}: {paths}")
        unique[name] = records[0]
    return unique



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


__all__ = [
    "CATALOGS",
    "LibraryCatalog",
    "LibraryEntryRecord",
    "candidate_records_from_inputs",
    "catalog_dir",
    "load_library_entry_source_for_record",
    "unique_records_from_candidates",
]
