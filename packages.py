"""NodeForge package inventory, manifest validation, and installation helpers.

A package is an isolated directory with a ``nodeforge_package.json`` manifest and
optional ``functions/``, ``examples/``, and ``systems/`` content roots. Runtime
availability is controlled only by active records in ``state.json`` under the
user package inventory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from types import SimpleNamespace
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .errors import CompileError

MANIFEST_NAME = "nodeforge_package.json"
STATE_SCHEMA_VERSION = 1
PACKAGE_SCHEMA_VERSION = 1
_PACKAGE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z0-9_]+)+$")
_PUBLIC_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INSTALL_TOKEN_RE = re.compile(r"^install_[A-Za-z0-9_\-]+$")
_TEST_PACKAGES_DIR: Path | None = None


def _current_nodeforge_version() -> tuple[int, ...]:
    """Return the running add-on version used for package compatibility checks."""
    try:
        from . import bl_info

        raw = bl_info.get("version", ())
    except Exception:
        raw = ()
    if not isinstance(raw, (tuple, list)) or not raw:
        raise PackageError("NodeForge runtime version is unavailable")
    try:
        return tuple(int(part) for part in raw)
    except Exception as exc:
        raise PackageError("NodeForge runtime version is malformed") from exc


def _parse_dotted_numeric_version(value: Any, field: str, *, nullable: bool = False) -> tuple[int, ...] | None:
    """Parse a manifest dotted numeric version field for deterministic comparison."""
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PackageError(f"Package manifest {field} must be a dotted numeric version string")
    parts = value.strip().split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise PackageError(f"Package manifest {field} must be a dotted numeric version string")
    return tuple(int(part) for part in parts)


def _compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """Compare dotted numeric version tuples with zero padding."""
    size = max(len(left), len(right))
    a = left + (0,) * (size - len(left))
    b = right + (0,) * (size - len(right))
    return (a > b) - (a < b)


def _validate_nodeforge_version_bounds(raw: dict[str, Any]) -> None:
    """Reject packages that declare incompatible NodeForge runtime bounds."""
    _parse_dotted_numeric_version(raw.get("version"), "version")
    min_version = _parse_dotted_numeric_version(raw.get("nodeforge_min_version"), "nodeforge_min_version")
    max_version = _parse_dotted_numeric_version(raw.get("nodeforge_max_version"), "nodeforge_max_version", nullable=True)
    current = _current_nodeforge_version()
    if _compare_versions(current, min_version) < 0:
        raise PackageError(
            f"Package requires NodeForge {raw.get('nodeforge_min_version')} or newer; current version is {'.'.join(str(p) for p in current)}"
        )
    if max_version is not None and _compare_versions(current, max_version) > 0:
        raise PackageError(
            f"Package supports NodeForge up to {raw.get('nodeforge_max_version')}; current version is {'.'.join(str(p) for p in current)}"
        )


def _validate_raw_posix_relative_path(value: Any, field: str) -> str:
    """Validate raw POSIX relative path text before pathlib can normalize segments."""
    if not isinstance(value, str) or not value.strip():
        raise PackageError(f"{field} must be a relative path string")
    raw = value.strip()
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise PackageError(f"{field} must be a non-empty relative path")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PackageError(f"{field} must not contain empty, . or .. segments")
    return raw


class PackageError(CompileError):
    """Raised when package metadata, state, or files are invalid."""


@dataclass(frozen=True)
class PackageManifest:
    """Validated package manifest and owning package root."""

    package_id: str
    name: str
    version: str
    author: str
    description: str
    root: Path
    contents: dict[str, str]
    permissions: dict[str, bool]
    origin: str = "user"

    def root_for(self, namespace: str) -> Path | None:
        """Return an existing declared content root for *namespace*."""
        rel = self.contents.get(namespace)
        if rel is None:
            return None
        path = (self.root / rel).resolve()
        if not _is_relative_to(path, self.root.resolve()) or not path.exists() or not path.is_dir():
            return None
        return path


@dataclass(frozen=True)
class LibraryRoot:
    """One manifest-declared library root contributed by an active package."""

    namespace: str
    path: Path
    package_id: str
    package_name: str
    package_version: str
    origin: str


@dataclass(frozen=True)
class SystemPackageRecord:
    """One system directory contributed by an active package."""

    package_id: str
    package_name: str
    package_version: str
    system_id: str
    root: Path
    module_path: Path | None
    origin: str
    permissions: dict[str, bool]
    interface_path: Path | None = None
    legacy_system_path: Path | None = None


@dataclass(frozen=True)
class PackageDiagnostic:
    """Diagnostic for a package record that could not become active."""

    package_id: str
    message: str
    path: str = ""
    name: str = ""
    version: str = ""
    origin: str = ""
    python_required: bool = False
    python_allowed: bool = False


@dataclass(frozen=True)
class ActivePackage:
    """Validated active package state record and computed diagnostics."""

    manifest: PackageManifest
    state_record: dict[str, Any]
    python_required: bool


def set_packages_dir_for_tests(path: Path | None) -> None:
    """Override the user package inventory path for ordinary CPython tests."""
    global _TEST_PACKAGES_DIR
    _TEST_PACKAGES_DIR = Path(path).resolve() if path is not None else None


def packages_dir() -> Path:
    """Return the user package inventory directory, creating it when possible."""
    if _TEST_PACKAGES_DIR is not None:
        _TEST_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        return _TEST_PACKAGES_DIR
    try:
        import bpy  # type: ignore

        return Path(bpy.utils.user_resource("DATAFILES", path="nodeforge/packages", create=True))
    except Exception:
        fallback = Path(os.environ.get("NODEFORGE_PACKAGES_DIR", Path(tempfile.gettempdir()) / "nodeforge/packages"))
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def installed_dir() -> Path:
    """Return the package inventory install directory."""
    path = packages_dir() / "installed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    """Return the active package state file path."""
    return packages_dir() / "state.json"


def _default_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "packages": {}}


def load_package_state() -> dict[str, Any]:
    """Load package state as untrusted JSON with a normalized top-level shape."""
    path = state_path()
    if not path.exists():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PackageError(f"Could not read package state: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageError("Package state must be a JSON object")
    if data.get("schema_version") != STATE_SCHEMA_VERSION:
        raise PackageError("Unsupported package state schema_version")
    if not isinstance(data.get("packages"), dict):
        data["packages"] = {}
    data.pop("seed_sources", None)
    return data


def save_package_state(state: dict[str, Any]) -> None:
    """Durably save package state using a same-directory temp file and replace."""
    root = packages_dir()
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, state_path())
        _fsync_directory(root)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync for crash-durable state replacement."""
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def invalidate_caches() -> None:
    """Invalidate package-related runtime caches."""
    try:
        from .systems import registry as systems_registry

        systems_registry.invalidate_cache()
    except Exception:
        pass


def validate_package_root(root: Path, *, origin: str = "user") -> PackageManifest:
    """Validate a directory package root and return its manifest."""
    root = Path(root).resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists() or not manifest_path.is_file():
        raise PackageError(f"Package is missing {MANIFEST_NAME}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PackageError(f"Could not parse {MANIFEST_NAME}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PackageError("Package manifest must be a JSON object")
    if raw.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise PackageError("Unsupported package manifest schema_version")
    package_id = _require_string(raw, "id")
    if not _PACKAGE_ID_RE.match(package_id):
        raise PackageError("Package id must be a dotted lowercase identifier")
    name = _require_string(raw, "name")
    version = _require_string(raw, "version")
    _validate_nodeforge_version_bounds(raw)
    author = str(raw.get("author") or "")
    description = str(raw.get("description") or "")
    contents_raw = raw.get("contents")
    if not isinstance(contents_raw, dict) or not contents_raw:
        raise PackageError("Package manifest contents must be a non-empty object")
    contents: dict[str, str] = {}
    for key in ("functions", "examples", "systems"):
        if key in contents_raw:
            rel = _validate_manifest_relative_path(contents_raw[key], f"contents.{key}")
            root_path = (root / rel).resolve()
            if not _is_relative_to(root_path, root) or not root_path.exists() or not root_path.is_dir():
                raise PackageError(f"Manifest {key} root does not exist inside package: {rel}")
            contents[key] = rel
    if not contents:
        raise PackageError("Package manifest must declare at least one supported content root")
    permissions_raw = raw.get("permissions") or {}
    if not isinstance(permissions_raw, dict):
        raise PackageError("Package permissions must be an object")
    permissions = {"python": bool(permissions_raw.get("python", False))}
    python_required = package_requires_python(root, contents)
    if python_required and not permissions["python"]:
        raise PackageError("Package contains Python or systems but permissions.python is false")
    _validate_root_contents(root, contents)
    return PackageManifest(package_id, name, version, author, description, root, contents, permissions, origin)


def _require_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PackageError(f"Package manifest {key} must be a non-empty string")
    return value.strip()


def _validate_manifest_relative_path(value: Any, field: str) -> str:
    raw = _validate_raw_posix_relative_path(value, field)
    path = PurePosixPath(raw)
    if path.is_absolute() or str(path) in {".", ""}:
        raise PackageError(f"{field} must be a non-empty relative path")
    return path.as_posix()


def _validate_root_contents(root: Path, contents: dict[str, str]) -> None:
    """Validate package content-root conventions without loading implementation code."""
    for namespace in ("functions", "examples"):
        rel = contents.get(namespace)
        if not rel:
            continue
        content_root = (root / rel).resolve()
        seen: dict[str, str] = {}
        for child in content_root.iterdir():
            public_name: str | None = None
            if child.name.startswith("__"):
                continue
            if child.is_file() and child.suffix in {".nf", ".nodeforge"}:
                public_name = child.stem
            elif child.is_dir() and not child.name.startswith("."):
                public_name = child.name
            if public_name is None:
                continue
            _validate_public_name(public_name, f"{namespace} entry")
            previous = seen.get(public_name)
            if previous is not None:
                raise PackageError(
                    f"Duplicate {namespace} library entry {public_name!r} inside package: {previous} and {child.name}"
                )
            seen[public_name] = child.name
    systems_rel = contents.get("systems")
    if systems_rel:
        systems_root = (root / systems_rel).resolve()
        for child in systems_root.iterdir():
            if not child.is_dir() or child.name.startswith(".") or child.name.startswith("__"):
                continue
            _validate_public_name(child.name, "system id")
            interface_path = child / "interface.py"
            legacy_path = child / "system.py"
            if not interface_path.is_file() and not legacy_path.is_file():
                raise PackageError(f"System {child.name!r} must contain interface.py or system.py")


def _validate_public_name(name: str, context: str) -> None:
    if not _PUBLIC_NAME_RE.match(name) or name.startswith("_"):
        raise PackageError(f"{context} {name!r} must be a valid public identifier")


def package_requires_python(root: Path, contents: dict[str, str]) -> bool:
    """Return whether current declared roots contain Python or systems."""
    if "systems" in contents:
        return True
    for rel in contents.values():
        content_root = (root / rel).resolve()
        if not _is_relative_to(content_root, root.resolve()) or not content_root.exists():
            continue
        for path in content_root.rglob("*"):
            if path.is_file() and path.suffix == ".py":
                return True
    return False


def validate_package_system_declarations(manifest: PackageManifest) -> None:
    """Validate retained v1 package-local system declarations.

    V2 declarations are normalized only by the install/replacement inventory
    path and root environment bootstrap. Keeping this validator v1-only prevents
    active-package/UI enumeration from becoming another v2 Python executor.
    """
    from .systems import registry as systems_registry

    names: dict[str, str] = {}
    try:
        records = system_package_records_from_manifests((manifest,))
        for record in records:
            if record.interface_path is not None:
                continue
            module = systems_registry._load_system_entrypoint(record)
            declared = systems_registry._read_constructors(module, record)
            for name in declared:
                if name in names:
                    raise PackageError(f"Duplicate system constructor {name!r} inside {manifest.package_id}")
                _validate_not_core_callable_name(name, manifest.package_id)
                names[name] = record.system_id
    except PackageError:
        raise
    except CompileError as exc:
        raise PackageError(str(exc)) from exc
    except Exception as exc:
        raise PackageError(f"Could not validate system declarations for {manifest.package_id}: {exc}") from exc

def _validate_not_core_callable_name(name: str, package_id: str) -> None:
    """Reject package-backed constructors that shadow core DSL callables."""
    from .builtins import registry as builtin_registry

    if name in builtin_registry.BUILTIN_NAMES or name in builtin_registry.CALLABLE_BUILTIN_NAMES:
        raise PackageError(
            f"System constructor {name!r} from {package_id} collides with core callable namespace"
        )


def active_package_records(include_invalid: bool = False) -> list[ActivePackage | PackageDiagnostic]:
    """Return validated active package records from the unified inventory."""
    state = load_package_state()
    records: list[ActivePackage | PackageDiagnostic] = []
    for package_id, record in sorted(state.get("packages", {}).items()):
        try:
            records.append(_active_package_from_state(package_id, record))
        except PackageError as exc:
            if include_invalid:
                records.append(_package_diagnostic_from_state(package_id, record, str(exc)))
    return records


def active_package_manifests(include_invalid: bool = False) -> list[PackageManifest | PackageDiagnostic]:
    """Return active package manifests or optional diagnostics."""
    out: list[PackageManifest | PackageDiagnostic] = []
    for record in active_package_records(include_invalid=include_invalid):
        if isinstance(record, ActivePackage):
            out.append(record.manifest)
        else:
            out.append(record)
    return out


def _structural_active_package_from_state(package_id: str, record: Any) -> ActivePackage:
    """Validate active package state without executing package declaration Python."""
    if not isinstance(record, dict):
        raise PackageError("Package state record must be an object")
    rel = _validate_state_installed_path(package_id, record.get("installed_path"))
    root = (packages_dir() / rel).resolve()
    if not root.exists() or not root.is_dir():
        raise PackageError("Installed package directory is missing")
    manifest = validate_package_root(root, origin=str(record.get("origin") or "user"))
    if manifest.package_id != package_id:
        raise PackageError("Manifest id does not match package state key")
    if manifest.version != record.get("installed_version"):
        raise PackageError("Manifest version does not match package state version")
    python_required = package_requires_python(manifest.root, manifest.contents)
    if python_required and not bool(record.get("allow_python", False)):
        raise PackageError("Package requires Python but Python consent is not recorded")
    return ActivePackage(manifest, record, python_required)


def active_package_manifest_snapshot() -> tuple[PackageManifest, ...]:
    """Return one structurally active manifest snapshot without declaration execution."""
    state = load_package_state()
    manifests: list[PackageManifest] = []
    for package_id, record in sorted(state.get("packages", {}).items()):
        try:
            active = _structural_active_package_from_state(package_id, record)
        except PackageError:
            continue
        manifests.append(active.manifest)
    return tuple(manifests)


def _active_package_from_state(package_id: str, record: Any) -> ActivePackage:
    """Return one active package while keeping v2 interface execution out of enumeration."""
    active = _structural_active_package_from_state(package_id, record)
    # Active-package/UI enumeration is intentionally structural for v2. Retained
    # v1 declarations may still be checked without creating a third v2 executor.
    validate_package_system_declarations(active.manifest)
    return active


def _package_diagnostic_from_state(package_id: str, record: Any, message: str) -> PackageDiagnostic:
    """Build UI-facing diagnostics from an invalid state record without activating it."""
    path = str(record.get("installed_path", "")) if isinstance(record, dict) else ""
    origin = str(record.get("origin", "")) if isinstance(record, dict) else ""
    python_allowed = bool(record.get("allow_python", False)) if isinstance(record, dict) else False
    name = package_id
    version = str(record.get("installed_version", "")) if isinstance(record, dict) else ""
    python_required = False
    if isinstance(record, dict):
        try:
            rel = _validate_state_installed_path(package_id, record.get("installed_path"))
            root = (packages_dir() / rel).resolve()
            manifest = validate_package_root(root, origin=origin or "user")
            name = manifest.name
            version = manifest.version
            python_required = package_requires_python(manifest.root, manifest.contents)
        except Exception:
            pass
    return PackageDiagnostic(
        package_id=package_id,
        message=message,
        path=path,
        name=name,
        version=version,
        origin=origin,
        python_required=python_required,
        python_allowed=python_allowed,
    )


def _validate_state_installed_path(package_id: str, value: Any) -> Path:
    try:
        raw = _validate_raw_posix_relative_path(value, "installed_path")
    except PackageError as exc:
        raise PackageError("installed_path must be a contained relative path") from exc
    path = PurePosixPath(raw)
    if path.is_absolute():
        raise PackageError("installed_path must be a contained relative path")
    parts = path.parts
    if len(parts) != 3 or parts[0] != "installed" or parts[1] != package_id or not _INSTALL_TOKEN_RE.match(parts[2]):
        raise PackageError("installed_path must match installed/<package_id>/install_<token>")
    resolved = (packages_dir() / Path(*parts)).resolve()
    if not _is_relative_to(resolved, packages_dir().resolve()):
        raise PackageError("installed_path resolves outside package inventory")
    return Path(*parts)


def package_manifests(include_invalid: bool = False):
    """Compatibility alias for active package manifests."""
    return active_package_manifests(include_invalid=include_invalid)


def library_roots_from_manifests(
    namespace: str,
    manifests: Iterable[PackageManifest],
) -> tuple[LibraryRoot, ...]:
    """Derive package library roots from an explicit validated manifest snapshot."""
    if namespace not in {"functions", "examples"}:
        return ()
    roots: list[LibraryRoot] = []
    for item in manifests:
        if isinstance(item, PackageDiagnostic):
            continue
        path = item.root_for(namespace)
        if path is not None:
            roots.append(LibraryRoot(namespace, path, item.package_id, item.name, item.version, item.origin))
    return tuple(roots)


def library_roots(namespace: str) -> list[LibraryRoot]:
    """Return active package roots for the requested library namespace."""
    return list(library_roots_from_manifests(namespace, active_package_manifests()))


def system_package_records_from_manifests(
    manifests: Iterable[PackageManifest],
) -> tuple[SystemPackageRecord, ...]:
    """Derive system entrypoint records from an explicit validated manifest snapshot."""
    records: list[SystemPackageRecord] = []
    for item in manifests:
        if isinstance(item, PackageDiagnostic):
            continue
        systems_root = item.root_for("systems")
        if systems_root is None:
            continue
        for child in sorted(systems_root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith(".") or child.name.startswith("__"):
                continue
            interface_path = child / "interface.py"
            legacy_path = child / "system.py"
            if interface_path.is_file():
                interface = interface_path.resolve()
                legacy = None
                module_path = None
            elif legacy_path.is_file():
                interface = None
                legacy = legacy_path.resolve()
                module_path = legacy
            else:
                continue
            records.append(
                SystemPackageRecord(
                    package_id=item.package_id,
                    package_name=item.name,
                    package_version=item.version,
                    system_id=child.name,
                    root=child.resolve(),
                    module_path=module_path,
                    origin=item.origin,
                    permissions=item.permissions,
                    interface_path=interface,
                    legacy_system_path=legacy,
                )
            )
    return tuple(records)


def system_package_records() -> list[SystemPackageRecord]:
    """Return active system directories discovered by package convention."""
    return list(system_package_records_from_manifests(active_package_manifests()))


def install_package_directory(source_dir: Path, *, allow_python: bool, replace: bool = False, origin: str = "user") -> PackageManifest:
    """Install a package after validating the exact copied bytes that may be published."""
    source_dir = Path(source_dir).resolve()
    manifest = validate_package_root(source_dir, origin=origin)
    current_python_required = package_requires_python(manifest.root, manifest.contents)
    if current_python_required and not allow_python:
        raise PackageError("Package requires executable Python; explicit consent is required")

    state = load_package_state()
    if manifest.package_id in state.get("packages", {}) and not replace:
        raise PackageError(f"Package {manifest.package_id!r} is already installed")

    record: dict[str, Any] | None = None
    committed = False
    old_record = None
    try:
        # Copy first. V2 declaration Python is normalized only from these exact
        # candidate bytes, never once from source and again from the installed copy.
        record = _install_validated_directory(
            source_dir, manifest, allow_python=allow_python, origin=origin
        )
        installed_root = (packages_dir() / record["installed_path"]).resolve()
        installed_manifest = validate_package_root(installed_root, origin=origin)
        candidate_inventory = _normalize_package_callable_inventory(
            installed_manifest,
            normalize_native_libraries=True,
        )

        new_state = load_package_state()
        packages_map = new_state.setdefault("packages", {})
        old_record = packages_map.get(manifest.package_id)
        if old_record is not None and not replace:
            raise PackageError(f"Package {manifest.package_id!r} is already installed")
        _validate_no_package_name_collisions(
            installed_manifest,
            ignore_package_id=manifest.package_id if replace else None,
            new_inventory=candidate_inventory,
        )
        packages_map[manifest.package_id] = record
        save_package_state(new_state)
        committed = True
    except Exception:
        if record is not None and not committed:
            _delete_old_install_dir_best_effort(manifest.package_id, record)
        raise
    if old_record:
        _delete_old_install_dir_best_effort(manifest.package_id, old_record)
    invalidate_caches()
    return validate_package_root((packages_dir() / record["installed_path"]).resolve(), origin=origin)


def _install_validated_directory(source_dir: Path, manifest: PackageManifest, *, allow_python: bool, origin: str) -> dict[str, Any]:
    """Copy one structurally validated package without executing declaration Python."""
    token = f"install_{uuid.uuid4().hex[:12]}"
    target_parent = installed_dir() / manifest.package_id
    target_parent.mkdir(parents=True, exist_ok=True)
    target = target_parent / token
    if target.exists():
        raise PackageError("Install target collision")
    shutil.copytree(source_dir, target, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    installed_manifest = validate_package_root(target, origin=origin)
    if installed_manifest.package_id != manifest.package_id or installed_manifest.version != manifest.version:
        raise PackageError("Copied package manifest identity changed during installation")
    if package_requires_python(installed_manifest.root, installed_manifest.contents) and not allow_python:
        raise PackageError("Package requires executable Python; explicit consent is required")
    rel = Path("installed") / manifest.package_id / token
    return {
        "installed_path": rel.as_posix(),
        "installed_version": installed_manifest.version,
        "allow_python": bool(allow_python),
        "origin": origin,
    }

def install_package_zip(zip_path: Path, *, allow_python: bool, replace: bool = False, origin: str = "user") -> PackageManifest:
    """Install a package from a hostile zip archive after safe extraction."""
    zip_path = Path(zip_path)
    staging = packages_dir() / ".staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=False)
    try:
        root = _extract_zip_to_staging(zip_path, staging)
        return install_package_directory(root, allow_python=allow_python, replace=replace, origin=origin)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def uninstall_package(package_id: str) -> None:
    """Remove a package record and safely delete files only when its pointer validates.

    Invalid package records must remain removable from the inventory. Their file
    paths are treated as untrusted and are not deleted unless full active-record
    validation succeeds.
    """
    state = load_package_state()
    packages = state.setdefault("packages", {})
    record = packages.get(package_id)
    if record is None:
        raise PackageError(f"Package {package_id!r} is not installed")

    install_root = None
    try:
        install_root = _active_package_from_state(package_id, record).manifest.root
    except Exception:
        pass

    packages.pop(package_id)
    save_package_state(state)
    invalidate_caches()
    if install_root is not None:
        _delete_validated_install_dir_best_effort(install_root)


def _delete_old_install_dir_best_effort(package_id: str, record: Any) -> None:
    """Delete a previous install directory only when its state pointer validates."""
    try:
        active = _active_package_from_state(package_id, record)
    except Exception:
        return
    _delete_validated_install_dir_best_effort(active.manifest.root)


def _delete_validated_install_dir_best_effort(path: Path) -> None:
    """Best-effort remove a directory that came from validated active package state."""
    try:
        root = path.resolve()
        if _is_relative_to(root, installed_dir().resolve()) and root.name.startswith("install_"):
            shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass


def _extract_zip_to_staging(zip_path: Path, staging: Path) -> Path:
    seen: set[str] = set()
    seen_casefold: set[str] = set()
    entries: list[tuple[zipfile.ZipInfo, PurePosixPath, bool]] = []
    manifest_candidates: list[PurePosixPath] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            is_dir = info.is_dir() or name.endswith("/")
            raw_name = name[:-1] if is_dir and name.endswith("/") else name
            try:
                checked_name = _validate_raw_posix_relative_path(raw_name, "zip member path")
            except PackageError as exc:
                raise PackageError(f"Unsafe zip member path: {name}") from exc
            rel = PurePosixPath(checked_name)
            if rel.is_absolute():
                raise PackageError(f"Unsafe zip member path: {name}")
            if any(part == "__pycache__" for part in rel.parts) or rel.suffix in {".pyc", ".pyo"}:
                raise PackageError(f"Unsupported Python cache artifact in package archive: {name}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode and stat.S_ISLNK(mode):
                raise PackageError(f"Unsupported special file in package archive: {name}")
            if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise PackageError(f"Unsupported special file in package archive: {name}")
            normalized = rel.as_posix()
            folded = normalized.casefold()
            if normalized in seen or folded in seen_casefold:
                raise PackageError(f"Duplicate zip destination path: {name}")
            seen.add(normalized)
            seen_casefold.add(folded)
            if rel.name == MANIFEST_NAME:
                if is_dir:
                    raise PackageError(f"Package archive manifest must be a file: {name}")
                manifest_candidates.append(rel)
            entries.append((info, rel, is_dir))
        package_root_rel = _zip_package_root_from_manifest_candidates(manifest_candidates, entries)
        staging_root = staging.resolve()
        for info, rel, is_dir in entries:
            dest = (staging / Path(*rel.parts)).resolve()
            if not _is_relative_to(dest, staging_root):
                raise PackageError(f"Zip member escapes staging root: {info.filename}")
            if is_dir:
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return (staging / Path(*package_root_rel.parts)).resolve() if package_root_rel.parts else staging


def _zip_package_root_from_manifest_candidates(
    manifest_candidates: list[PurePosixPath],
    entries: list[tuple[zipfile.ZipInfo, PurePosixPath, bool]],
) -> PurePosixPath:
    """Return the only allowed package root layout for a safe zip archive."""
    if len(manifest_candidates) != 1:
        raise PackageError(f"Package archive must contain exactly one {MANIFEST_NAME}")
    manifest = manifest_candidates[0]
    if len(manifest.parts) == 1:
        return PurePosixPath()
    if len(manifest.parts) != 2:
        raise PackageError(f"Package archive must contain {MANIFEST_NAME} at root or one top-level directory")
    top = manifest.parts[0]
    for _info, rel, _is_dir in entries:
        if rel.parts and not rel.parts[0].startswith(".") and rel.parts[0] != top:
            raise PackageError(f"Package archive with nested {MANIFEST_NAME} must contain one top-level package directory")
    return PurePosixPath(top)


@dataclass(frozen=True)
class _PackageCallableInventory:
    """Temporary install-validation inventory detached from owner Python objects."""

    systems: dict[str, str]
    libraries: dict[str, dict[str, Path]]


def _normalize_package_callable_inventory(
    manifest: PackageManifest,
    *,
    normalize_native_libraries: bool,
) -> _PackageCallableInventory:
    """Normalize one package's callable inventory exactly once for validation."""
    from .systems import registry as systems_registry
    from .extension_registry import (
        ExtensionOwnerSession,
        capture_owner_code_snapshot,
        library_owner_key,
        system_owner_key,
    )

    system_names: dict[str, str] = {}
    try:
        for record in system_package_records_from_manifests((manifest,)):
            if record.interface_path is not None:
                session = ExtensionOwnerSession(
                    capture_owner_code_snapshot(system_owner_key(record), record.root)
                )
                families, _refs = session.normalize_interface()
                declared = tuple(callable_id.name for callable_id in families)
            else:
                module = systems_registry._load_system_entrypoint(record)
                declared = systems_registry._read_constructors(module, record)
            for name in declared:
                if name in system_names:
                    raise PackageError(
                        f"Duplicate system constructor {name!r} inside {manifest.package_id}"
                    )
                _validate_not_core_callable_name(name, manifest.package_id)
                system_names[name] = record.system_id

        libraries: dict[str, dict[str, Path]] = {"functions": {}, "examples": {}}
        for namespace in ("functions", "examples"):
            root = manifest.root_for(namespace)
            if root is None:
                continue
            entries = _public_entry_entries(root)
            libraries[namespace] = dict(entries)
            if not normalize_native_libraries:
                continue
            for name, path in entries.items():
                if not path.is_dir():
                    continue
                interface_path = path / "interface.py"
                if not interface_path.is_file():
                    continue
                if (path / "source.nf").is_file():
                    raise PackageError(
                        f"V2 hybrid library owner {namespace}/{name} is not supported by this backend-only platform"
                    )
                record = SimpleNamespace(
                    namespace=namespace,
                    name=name,
                    package_id=manifest.package_id,
                )
                session = ExtensionOwnerSession(
                    capture_owner_code_snapshot(library_owner_key(record), path.resolve())
                )
                families, _refs = session.normalize_interface()
                names = tuple(callable_id.name for callable_id in families)
                if names != (name,):
                    raise PackageError(
                        f"Native-only library {namespace}/{name} must declare exactly one EXTENSIONS key equal to {name!r}"
                    )
        return _PackageCallableInventory(system_names, libraries)
    except PackageError:
        raise
    except CompileError as exc:
        raise PackageError(str(exc)) from exc
    except Exception as exc:
        raise PackageError(f"Could not validate callable declarations for {manifest.package_id}: {exc}") from exc


def _existing_package_validation_inventories(
    *,
    ignore_package_id: str | None,
) -> list[tuple[PackageManifest, _PackageCallableInventory]]:
    """Return declaration-valid installed package inventories for collision checks."""
    state = load_package_state()
    inventories = []
    for package_id, record in sorted(state.get("packages", {}).items()):
        if package_id == ignore_package_id:
            continue
        try:
            active = _structural_active_package_from_state(package_id, record)
            inventory = _normalize_package_callable_inventory(
                active.manifest,
                normalize_native_libraries=False,
            )
        except PackageError:
            # Preserve package-atomic activation: declaration-invalid packages do
            # not contribute systems or package library roots to this environment.
            continue
        inventories.append((active.manifest, inventory))
    return inventories


def _validate_no_package_name_collisions(
    new_manifest: PackageManifest,
    *,
    ignore_package_id: str | None = None,
    new_inventory: _PackageCallableInventory | None = None,
) -> None:
    """Reject public names that would become ambiguous after installation."""
    candidate = new_inventory or _normalize_package_callable_inventory(
        new_manifest,
        normalize_native_libraries=True,
    )
    existing_by_namespace: dict[str, dict[str, tuple[str, Path]]] = {
        "functions": {},
        "examples": {},
    }
    existing_system_names: dict[str, tuple[str, str]] = {}
    for manifest, inventory in _existing_package_validation_inventories(
        ignore_package_id=ignore_package_id
    ):
        for namespace in ("functions", "examples"):
            for name, path in inventory.libraries[namespace].items():
                existing_by_namespace[namespace][name] = (manifest.package_id, path)
        for name, system_id in inventory.systems.items():
            existing_system_names[name] = (manifest.package_id, system_id)

    for namespace in ("functions", "examples"):
        for name, path in candidate.libraries[namespace].items():
            owner = existing_by_namespace[namespace].get(name)
            if owner is not None:
                raise PackageError(
                    f"Duplicate {namespace} library entry {name!r}: "
                    f"{owner[0]} at {owner[1]} and {new_manifest.package_id} at {path}"
                )

    new_function_names = candidate.libraries["functions"]
    for name, path in new_function_names.items():
        owner = existing_system_names.get(name)
        if owner is not None:
            raise PackageError(
                f"Public name collision {name!r}: function from {new_manifest.package_id} at {path} "
                f"and constructor from {owner[0]}/{owner[1]}"
            )

    for name, system_id in candidate.systems.items():
        owner = existing_system_names.get(name)
        if owner is not None:
            raise PackageError(
                f"Duplicate system constructor {name!r}: {owner[0]}/{owner[1]} and {new_manifest.package_id}/{system_id}"
            )
        function_owner = existing_by_namespace["functions"].get(name)
        if function_owner is not None:
            raise PackageError(
                f"Public name collision {name!r}: constructor from {new_manifest.package_id}/{system_id} "
                f"and function from {function_owner[0]} at {function_owner[1]}"
            )
        function_path = new_function_names.get(name)
        if function_path is not None:
            raise PackageError(
                f"Public name collision {name!r}: function and constructor both declared by "
                f"{new_manifest.package_id} ({function_path} and system {system_id})"
            )


def _public_entry_entries(root: Path) -> dict[str, Path]:
    names: dict[str, Path] = {}
    for child in root.iterdir():
        if child.name.startswith("__"):
            continue
        if child.is_file() and child.suffix in {".nf", ".nodeforge"}:
            names[child.stem] = child
        elif child.is_dir() and _PUBLIC_NAME_RE.match(child.name):
            names[child.name] = child
    return names


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


__all__ = [
    "MANIFEST_NAME",
    "PackageError",
    "PackageManifest",
    "LibraryRoot",
    "SystemPackageRecord",
    "PackageDiagnostic",
    "set_packages_dir_for_tests",
    "packages_dir",
    "load_package_state",
    "save_package_state",
    "validate_package_root",
    "package_requires_python",
    "validate_package_system_declarations",
    "active_package_records",
    "active_package_manifests",
    "package_manifests",
    "library_roots",
    "system_package_records",
    "install_package_directory",
    "install_package_zip",
    "uninstall_package",
    "invalidate_caches",
]
