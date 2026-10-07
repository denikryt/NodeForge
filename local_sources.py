"""Filesystem-backed Local source registry and managed source storage."""

from __future__ import annotations

import errno
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

from .errors import CompileError
from . import catalog


_LOCAL_SOURCES_REGISTRY_VERSION = 1
_SOURCE_EXTENSIONS = (".nf", ".nodeforge")

def _local_sources_registry_path() -> Path:
    """Return the user-owned registry file for externally linked Local source folders."""
    import bpy

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


def _default_local_catalog_dir() -> Path:
    """Return the persistent user-owned root for Local catalog sources."""
    import bpy

    return Path(bpy.utils.user_resource("DATAFILES", path="nodeforge/local", create=True))


def _catalog_input_paths() -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Return already-resolved Local roots/files for pure catalog discovery."""
    ensure_local_catalog_dir()
    registry = tuple(_read_local_source_registry())
    roots = tuple(Path(record["path"]) for record in _local_source_roots_from_registry(registry))
    files = tuple(Path(record["path"]) for record in _local_source_files_from_registry(registry))
    return roots, files


def local_candidate_records() -> tuple[catalog.LibraryEntryRecord, ...]:
    """Return raw Local catalog records from the current filesystem snapshot."""
    roots, files = _catalog_input_paths()
    return tuple(
        catalog.candidate_records_from_inputs(
            "local", package_roots=(), local_roots=roots, local_files=files
        )
    )


def local_entry_records() -> tuple[catalog.LibraryEntryRecord, ...]:
    """Return unique Local source records in deterministic name order."""
    entries = catalog.unique_records_from_candidates("local", local_candidate_records())
    return tuple(sorted(entries.values(), key=lambda record: record.name.lower()))


def local_entry_names() -> set[str]:
    """Return current public Local source names."""
    return {record.name for record in local_entry_records()}


def find_local_entry_record(name: str) -> catalog.LibraryEntryRecord | None:
    """Return one current Local source record by public name."""
    catalog._validate_public_entry_name(name, "local library entry")
    entries = catalog.unique_records_from_candidates("local", local_candidate_records())
    return entries.get(name)


def has_local_entry(name: str) -> bool:
    """Return whether one valid Local source name is currently available."""
    if not catalog._is_public_function_name(name):
        return False
    return find_local_entry_record(name) is not None


def load_local_entry_source(name: str) -> str:
    """Load one current Local source by public name."""
    record = find_local_entry_record(name)
    if record is None:
        raise CompileError(f"local library entry {name!r} has no editable .nf source")
    return catalog.load_library_entry_source_for_record(record)


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

    script_by_path = {
        _path_key(record.path): record
        for record in local_candidate_records()
    }
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
def ensure_local_catalog_dir() -> Path:
    """Create and return the user-owned local catalog directory."""
    path = _default_local_catalog_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_library_entry_name(raw: str) -> str:
    """Validate a user-facing public script name for the local catalog."""
    name = (raw or "").strip()
    return catalog._validate_public_entry_name(name, "Local script")


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
        if not catalog._is_public_function_name(part):
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
