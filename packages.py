"""NodeForge package inventory, manifest validation, and installation helpers.

A package is an isolated directory with a ``nodeforge_package.json`` manifest and
optional ``functions/``, ``examples/``, and ``systems/`` content roots. Runtime
availability is controlled only by active records in ``state.json`` under the
user package inventory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import CompileError

MANIFEST_NAME = "nodeforge_package.json"
STATE_SCHEMA_VERSION = 1
PACKAGE_SCHEMA_VERSION = 1
SHIPPED_PACKAGE_IDS = ("nodeforge.standard", "nodeforge.lsystem")
_PACKAGE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z0-9_]+)+$")
_PUBLIC_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INSTALL_TOKEN_RE = re.compile(r"^install_[A-Za-z0-9_\-]+$")
_TEST_PACKAGES_DIR: Path | None = None
_SEEDING_PACKAGES = False


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
    module_path: Path
    origin: str
    permissions: dict[str, bool]


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


def package_seed_sources_dir() -> Path:
    """Return the NodeForge-shipped package source directory."""
    return Path(__file__).resolve().parent / "packages"


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
    return {"schema_version": STATE_SCHEMA_VERSION, "seed_sources": {}, "packages": {}}


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
    if not isinstance(data.get("seed_sources"), dict):
        data["seed_sources"] = {}
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


def ensure_seeded_packages() -> None:
    """Seed or refresh active NodeForge-shipped package sources in the normal inventory."""
    global _SEEDING_PACKAGES
    if _SEEDING_PACKAGES:
        return
    _SEEDING_PACKAGES = True
    try:
        state = load_package_state()
        changed = False
        for package_id in SHIPPED_PACKAGE_IDS:
            source = package_seed_sources_dir() / package_id
            if not source.exists():
                continue
            source_marker = _seed_source_marker(source)
            seeded = _seed_source_matches(state["seed_sources"].get(package_id), source_marker)
            active = package_id in state["packages"]
            if seeded or (not active and package_id in state["seed_sources"]):
                # A matching active source needs no work. A previously seeded but
                # currently absent source was intentionally uninstalled by the user.
                continue
            manifest = validate_package_root(source, origin="nodeforge_shipped_source")
            if manifest.package_id != package_id:
                raise PackageError(f"Shipped source {source} declares {manifest.package_id!r}, expected {package_id!r}")
            validate_package_system_declarations(manifest)
            _validate_no_package_name_collisions(manifest, ignore_package_id=package_id if active else None)
            record = _install_validated_directory(source, manifest, allow_python=True, origin="nodeforge_shipped")
            state = load_package_state()
            old_record = state.setdefault("packages", {}).get(package_id)
            state["packages"][package_id] = record
            state.setdefault("seed_sources", {})[package_id] = source_marker
            save_package_state(state)
            if old_record:
                _delete_old_install_dir_best_effort(package_id, old_record)
            changed = True
        if changed:
            invalidate_caches()
    finally:
        _SEEDING_PACKAGES = False


def _seed_source_marker(source: Path) -> dict[str, str]:
    """Return product-semantic metadata for the current shipped package source."""
    manifest = validate_package_root(source, origin="nodeforge_shipped_source")
    return {"source_version": manifest.version, "fingerprint": _package_source_fingerprint(source)}


def _seed_source_matches(value: Any, marker: dict[str, str]) -> bool:
    """Return True when an existing seed marker matches the current source."""
    return isinstance(value, dict) and value.get("source_version") == marker["source_version"] and value.get("fingerprint") == marker["fingerprint"]


def _package_source_fingerprint(source: Path) -> str:
    """Hash the shipped source tree so active seeded packages can refresh after add-on updates."""
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*"), key=lambda p: p.relative_to(source).as_posix().casefold()):
        rel = path.relative_to(source)
        if any(part == "__pycache__" for part in rel.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        digest.update(rel.as_posix().encode("utf-8"))
        if path.is_file():
            digest.update(b"\0file\0")
            digest.update(path.read_bytes())
        elif path.is_dir():
            digest.update(b"\0dir\0")
    return digest.hexdigest()


def install_package_source(package_id: str) -> None:
    """Install or repair one NodeForge-provided package source as a normal package."""
    if package_id not in SHIPPED_PACKAGE_IDS:
        raise PackageError(f"Unknown package source: {package_id}")
    source = package_seed_sources_dir() / package_id
    install_package_directory(source, allow_python=True, replace=True, origin="nodeforge_shipped")
    state = load_package_state()
    state.setdefault("seed_sources", {})[package_id] = _seed_source_marker(source)
    save_package_state(state)


def reinstall_shipped_package(package_id: str) -> None:
    """Compatibility wrapper for installing a NodeForge-provided package source."""
    install_package_source(package_id)


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
            if not (child / "system.py").is_file():
                raise PackageError(f"System {child.name!r} must contain system.py")


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
    """Validate lightweight system entrypoint declarations for one package.

    This imports only each conventional ``system.py`` declaration module. It
    validates ``CONSTRUCTORS`` and the presence of callable ``load_handlers()``
    without calling ``load_handlers()`` or importing runtime handler modules.
    """
    try:
        names = _system_constructor_names_for_manifest(manifest)
    except PackageError:
        raise
    except CompileError as exc:
        raise PackageError(str(exc)) from exc
    except Exception as exc:
        raise PackageError(f"Could not validate system declarations for {manifest.package_id}: {exc}") from exc
    if len(names) != len(set(names)):
        raise PackageError(f"Duplicate system constructor inside {manifest.package_id}")
    for name in names:
        _validate_not_core_callable_name(name, manifest.package_id)


def _validate_not_core_callable_name(name: str, package_id: str) -> None:
    """Reject package-backed constructors that shadow core DSL callables."""
    from .builtins import registry as builtin_registry

    if name in builtin_registry.BUILTIN_NAMES or name in builtin_registry.CALLABLE_BUILTIN_NAMES:
        raise PackageError(
            f"System constructor {name!r} from {package_id} collides with core callable namespace"
        )


def active_package_records(include_invalid: bool = False) -> list[ActivePackage | PackageDiagnostic]:
    """Return validated active package records from the unified inventory."""
    ensure_seeded_packages()
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


def _active_package_from_state(package_id: str, record: Any) -> ActivePackage:
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
    validate_package_system_declarations(manifest)
    return ActivePackage(manifest, record, python_required)


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


def library_roots(namespace: str) -> list[LibraryRoot]:
    """Return active package roots for the requested library namespace."""
    if namespace not in {"functions", "examples"}:
        return []
    roots: list[LibraryRoot] = []
    for item in active_package_manifests():
        if isinstance(item, PackageDiagnostic):
            continue
        path = item.root_for(namespace)
        if path is not None:
            roots.append(LibraryRoot(namespace, path, item.package_id, item.name, item.version, item.origin))
    return roots


def system_package_records() -> list[SystemPackageRecord]:
    """Return active system directories discovered by package convention."""
    records: list[SystemPackageRecord] = []
    for item in active_package_manifests():
        if isinstance(item, PackageDiagnostic):
            continue
        systems_root = item.root_for("systems")
        if systems_root is None:
            continue
        for child in sorted(systems_root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith(".") or child.name.startswith("__"):
                continue
            module_path = child / "system.py"
            if not module_path.is_file():
                continue
            records.append(
                SystemPackageRecord(
                    package_id=item.package_id,
                    package_name=item.name,
                    package_version=item.version,
                    system_id=child.name,
                    root=child.resolve(),
                    module_path=module_path.resolve(),
                    origin=item.origin,
                    permissions=item.permissions,
                )
            )
    return records


def install_package_directory(source_dir: Path, *, allow_python: bool, replace: bool = False, origin: str = "user") -> PackageManifest:
    """Install a package from an already-unpacked directory."""
    source_dir = Path(source_dir).resolve()
    manifest = validate_package_root(source_dir, origin=origin)
    current_python_required = package_requires_python(manifest.root, manifest.contents)
    if current_python_required and not allow_python:
        raise PackageError("Package requires executable Python; explicit consent is required")
    validate_package_system_declarations(manifest)
    state = load_package_state()
    if manifest.package_id in state.get("packages", {}) and not replace:
        raise PackageError(f"Package {manifest.package_id!r} is already installed")
    _validate_no_package_name_collisions(manifest, ignore_package_id=manifest.package_id if replace else None)
    record: dict[str, Any] | None = None
    committed = False
    try:
        record = _install_validated_directory(source_dir, manifest, allow_python=allow_python, origin=origin)
        new_state = load_package_state()
        packages_map = new_state.setdefault("packages", {})
        old_record = packages_map.get(manifest.package_id)
        if old_record is not None and not replace:
            raise PackageError(f"Package {manifest.package_id!r} is already installed")
        _validate_no_package_name_collisions(manifest, ignore_package_id=manifest.package_id if replace else None)
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
    token = f"install_{uuid.uuid4().hex[:12]}"
    target_parent = installed_dir() / manifest.package_id
    target_parent.mkdir(parents=True, exist_ok=True)
    target = target_parent / token
    if target.exists():
        raise PackageError("Install target collision")
    shutil.copytree(source_dir, target, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    installed_manifest = validate_package_root(target, origin=origin)
    validate_package_system_declarations(installed_manifest)
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
    """Remove a validated package from future discovery and best-effort delete its files."""
    ensure_seeded_packages()
    state = load_package_state()
    packages = state.setdefault("packages", {})
    record = packages.get(package_id)
    if record is None:
        raise PackageError(f"Package {package_id!r} is not installed")
    active = _active_package_from_state(package_id, record)
    packages.pop(package_id)
    save_package_state(state)
    invalidate_caches()
    _delete_validated_install_dir_best_effort(active.manifest.root)


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


def _validate_no_package_name_collisions(new_manifest: PackageManifest, *, ignore_package_id: str | None = None) -> None:
    """Reject public names that would become ambiguous after installation."""
    existing_by_namespace: dict[str, dict[str, tuple[str, Path]]] = {"functions": {}, "examples": {}}
    for item in active_package_manifests():
        if isinstance(item, PackageDiagnostic) or item.package_id == ignore_package_id:
            continue
        for namespace in ("functions", "examples"):
            root = item.root_for(namespace)
            if root is None:
                continue
            for name, path in _public_entry_entries(root).items():
                existing_by_namespace[namespace][name] = (item.package_id, path)
    existing_constructor_names: dict[str, tuple[str, str]] = {}
    try:
        from .systems import registry as systems_registry

        for record in system_package_records():
            if record.package_id == ignore_package_id:
                continue
            module = systems_registry._load_system_entrypoint(record)
            for name in systems_registry._read_constructors(module, record):
                existing_constructor_names[name] = (record.package_id, record.system_id)
    except PackageError:
        raise
    except CompileError as exc:
        raise PackageError(str(exc)) from exc

    new_function_names: dict[str, Path] = {}
    for namespace in ("functions", "examples"):
        root = new_manifest.root_for(namespace)
        if root is None:
            continue
        entries = _public_entry_entries(root)
        if namespace == "functions":
            new_function_names = entries
        for name, path in entries.items():
            owner = existing_by_namespace[namespace].get(name)
            if owner is not None:
                raise PackageError(
                    f"Duplicate {namespace} library entry {name!r}: "
                    f"{owner[0]} at {owner[1]} and {new_manifest.package_id} at {path}"
                )
    for name, path in new_function_names.items():
        owner = existing_constructor_names.get(name)
        if owner is not None:
            raise PackageError(
                f"Public name collision {name!r}: function from {new_manifest.package_id} at {path} "
                f"and constructor from {owner[0]}/{owner[1]}"
            )

    new_constructor_names = _system_constructor_entries_for_manifest(new_manifest)
    for name, system_id in new_constructor_names.items():
        owner = existing_constructor_names.get(name)
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



def _system_constructor_names_for_manifest(manifest: PackageManifest) -> set[str]:
    """Read lightweight system declarations for a package root without handlers."""
    return set(_system_constructor_entries_for_manifest(manifest))


def _system_constructor_entries_for_manifest(manifest: PackageManifest) -> dict[str, str]:
    """Read constructor declarations for a package root as name -> system id."""
    systems_root = manifest.root_for("systems")
    if systems_root is None:
        return {}
    from .systems import registry as systems_registry

    names: dict[str, str] = {}
    for child in sorted(systems_root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith(".") or child.name.startswith("__"):
            continue
        module_path = child / "system.py"
        if not module_path.is_file():
            continue
        record = SystemPackageRecord(
            package_id=manifest.package_id,
            package_name=manifest.name,
            package_version=manifest.version,
            system_id=child.name,
            root=child.resolve(),
            module_path=module_path.resolve(),
            origin=manifest.origin,
            permissions=manifest.permissions,
        )
        module = systems_registry._load_system_entrypoint(record)
        for name in systems_registry._read_constructors(module, record):
            if name in names:
                raise PackageError(f"Duplicate system constructor {name!r} inside {manifest.package_id}")
            names[name] = child.name
    return names


def _public_entry_names(root: Path) -> set[str]:
    return set(_public_entry_entries(root))


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
    "package_seed_sources_dir",
    "packages_dir",
    "load_package_state",
    "save_package_state",
    "ensure_seeded_packages",
    "install_package_source",
    "reinstall_shipped_package",
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
