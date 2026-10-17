"""Blender-independent catalog records and source discovery."""

from __future__ import annotations

import re
import keyword
import os
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
    source_origin: str = ""

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


def _walk_public_source_tree(root: Path, prefix=(), *, walker=os.walk):
    """Yield deterministic directories and candidate file positions without following directory links.

    The caller owns namespace-specific acceptance of files. Filesystem traversal
    errors propagate, and non-public parents never become importable positions.
    """
    def fail(error):
        """Reject partially traversed source trees."""
        raise error
    for current, children, files in walker(root, followlinks=False, onerror=fail):
        children[:] = sorted(name for name in children if not name.startswith(("_", "."))
                             and not (Path(current) / name).is_symlink())
        relative = Path(current).relative_to(root).parts
        physical_prefix = prefix + relative
        import_prefix = physical_prefix if all(is_public_local_name(x) for x in physical_prefix) else None
        yield (Path(current), physical_prefix, import_prefix, (
            name for name in files
            if not name.startswith(("_", ".")) and (name == _PACKAGE_SOURCE_NAME or _eligible_local_stem(name) is not None)
        ))


def _example_source_positions(root, *, package_id, package_name, package_version):
    """Capture all ordinary nested Examples source leaves and physical directories."""
    records, directories, positions = [], [], []
    if not root.exists():
        return records, directories, positions
    if not root.is_dir():
        raise CompileError(f"Examples catalog root is not a directory: {root}")
    canonical_root = _canonical_path(root)
    for current, physical_prefix, import_prefix, files in _walk_public_source_tree(root):
        if physical_prefix:
            directories.append((physical_prefix, _path_key(current)))
        if import_prefix is None:
            continue
        for name in sorted(files):
            path = current / name
            if path.suffix not in _SOURCE_EXTENSIONS or not is_public_local_name(path.stem):
                continue
            # source.nf always names its containing legacy directory owner.
            if name == _PACKAGE_SOURCE_NAME and physical_prefix:
                continue
            if path.is_symlink() and not _canonical_path(path).is_relative_to(canonical_root):
                raise CompileError(f"Examples source resolves outside its catalog root: {path}")
            if not path.is_file():
                continue
            canonical = _canonical_path(path)
            if not canonical.is_relative_to(canonical_root):
                raise CompileError(f"Examples source resolves outside its catalog root: {path}")
            if not physical_prefix:
                # Top-level source records are already captured by the legacy catalog.
                continue
            record = LibraryEntryRecord(
                namespace="examples", name=path.stem, kind="script", path=canonical,
                source_path=canonical, folder_path="/".join(physical_prefix),
                source_origin=_path_key(canonical), package_id=package_id,
                package_name=package_name, package_version=package_version,
            )
            records.append(record)
            positions.append((physical_prefix, record.name, record.source_origin))
    return records, directories, positions


class ExamplesCandidateRecords(list):
    """One Examples scan retaining discovered directory positions beside legacy records.

    The list interface preserves the established raw candidate API. Positions
    belong to the same inventory and are consumed when a catalog is frozen.
    """

    def __init__(self, records, directories, source_positions):
        """Keep one selected inventory and its physical directory descriptors."""
        super().__init__(records)
        self.directories = tuple(directories)
        self.source_positions = tuple(source_positions)


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
    if namespace == "local":
        return list(discover_local_sources(local_roots, local_files)[0])
    roots: list[tuple[Path, str, str, str]] = []
    if namespace == "examples" and include_builtin_catalog:
        roots.append((catalog_dir(namespace), "", "", ""))
    roots.extend(
        (root.path, root.package_id, root.package_name, root.package_version)
        for root in package_roots
    )

    records: list[LibraryEntryRecord] = []
    directories, positions = [], []
    for root, package_id, package_name, package_version in roots:
        if not root.exists():
            continue
        root_origin = _canonical_path(root)
        for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if namespace == "examples" and path.is_symlink() and path.is_dir():
                continue
            if namespace == "examples" and path.is_file() and not _canonical_path(path).is_relative_to(root_origin):
                raise CompileError(f"Examples source resolves outside its catalog root: {path}")
            if path.name == "__init__.py" or path.name.startswith("__"):
                continue
            if path.is_file():
                if path.suffix in _SOURCE_EXTENSIONS and _is_public_function_name(path.stem):
                    records.append(
                        LibraryEntryRecord(
                            namespace=namespace,
                            name=path.stem,
                            kind="script",
                            path=_canonical_path(path),
                            source_path=_canonical_path(path),
                            source_origin=_path_key(path) if namespace == "examples" else "",
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
                if namespace == "examples":
                    for candidate in (source_path, interface_path, legacy_module_path):
                        if candidate is not None and not _canonical_path(candidate).is_relative_to(root_origin):
                            raise CompileError(f"Examples owner resolves outside its catalog root: {candidate}")
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
                            path=_canonical_path(source_path or interface_path) if namespace == "examples" else source_path or interface_path,
                            source_path=_canonical_path(source_path) if source_path is not None and namespace == "examples" else source_path,
                            source_origin=_path_key(source_path or interface_path) if namespace == "examples" else "",
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
                            path=_canonical_path(source_path or legacy_module_path or path) if namespace == "examples" else source_path or legacy_module_path or path,
                            source_path=_canonical_path(source_path) if source_path is not None and namespace == "examples" else source_path,
                            source_origin=_path_key(source_path or legacy_module_path or path) if namespace == "examples" else "",
                            module_path=_canonical_path(legacy_module_path) if namespace == "examples" and legacy_module_path is not None else legacy_module_path,
                            interface_path=None,
                            package_id=package_id,
                            package_name=package_name,
                            package_version=package_version,
                        )
                    )

        if namespace == "examples":
            nested, discovered_dirs, discovered_positions = _example_source_positions(
                root, package_id=package_id, package_name=package_name,
                package_version=package_version,
            )
            records.extend(nested)
            directories.extend(discovered_dirs)
            positions.extend(discovered_positions)
            # Legacy directory owners must also be reachable at their physical position.
            for record in records:
                if record.namespace == "examples" and record.folder_path == "" and record.path.is_relative_to(_canonical_path(root)):
                    if record.source_origin:
                        positions.append(((), record.name, record.source_origin))
    return ExamplesCandidateRecords(records, directories, positions) if namespace == "examples" else records


def _canonical_path(path: Path | str) -> Path:
    """Resolve a physical source path without requiring the target to exist."""
    return Path(path).expanduser().resolve(strict=False)


def _path_key(path: Path | str) -> str:
    """Return the platform-normalized canonical pathname identity."""
    return os.path.normcase(str(_canonical_path(path)))


def is_public_local_name(name: str) -> bool:
    """Require an entire public ASCII identifier usable in Python import syntax."""
    return _is_public_function_name(name) and name.isidentifier() and not keyword.iskeyword(name)


def _eligible_local_stem(name: str) -> str | None:
    """Filter ordinary Local filenames without allocating or probing a path."""
    stem, separator, suffix = name.rpartition(".")
    if not separator or "." + suffix not in _SOURCE_EXTENSIONS:
        return None
    return stem if is_public_local_name(stem) else None


def discover_local_sources(local_roots, local_files=()):
    """Capture Local source records and empty directory positions in one traversal.

    The first root is managed; subsequent roots contribute their physical basename.
    Directory symlinks are not traversed. File symlinks must stay inside their root.
    IO failures propagate rather than publishing an incomplete snapshot.
    """
    records = []
    directories = []
    source_positions = []

    def add_source(path, root, prefix):
        """Record one eligible source with a canonical owner and optional import path."""
        if not path.is_file():
            return
        if path.name == _PACKAGE_SOURCE_NAME:
            raise CompileError(f"Unsupported local source layout: {path}")
        if path.suffix not in _SOURCE_EXTENSIONS or not is_public_local_name(path.stem):
            return
        canonical = _canonical_path(path)
        if root is not None:
            try:
                canonical.relative_to(root)
            except ValueError as exc:
                raise CompileError(f"Local source resolves outside its registered root: {path}") from exc
        if not is_public_local_name(canonical.stem):
            return
        record = LibraryEntryRecord(
            namespace="local", name=canonical.stem, kind="script", path=canonical,
            source_path=canonical, source_origin=_path_key(canonical),
            folder_path="" if root is None else _relative_folder_for_path(root, path, path.stem),
        )
        records.append(record)
        if prefix is not None and path.stem == record.name:
            source_positions.append((prefix, record.name, record.source_origin))

    for root_number, root_input in enumerate(local_roots):
        root = _canonical_path(_path_value(root_input))
        if not root.exists():
            continue
        if not root.is_dir():
            raise CompileError(f"Local source root is not a directory: {root}")
        prefix = () if root_number == 0 else (root.name,)
        if root_number:
            directories.append((prefix, _path_key(root)))
        for current, physical_prefix, current_prefix, files in _walk_public_source_tree(root, prefix, walker=os.walk):
            if current != root:
                directories.append((physical_prefix, _path_key(current)))
            # Preserve walker order while sorting only potential sources. source.nf
            # retains its is_file-before-layout-error check, including broken links.
            candidates = (
                name for name in files
                if not name.startswith(("_", "."))
                and (name == _PACKAGE_SOURCE_NAME or _eligible_local_stem(name) is not None)
            )
            for name in sorted(candidates):
                add_source(Path(current) / name, root, current_prefix)
    for linked in local_files:
        path = _path_value(linked)
        if path.is_file():
            add_source(path, None, None)
    return tuple(records), tuple(directories), tuple(source_positions)


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
