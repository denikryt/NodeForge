"""Snapshot-backed owner sessions and immutable declarative extension registry."""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.util
import os
import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from .compiler_identities import normalize_library_package_id
from .errors import CompileError
from .extension_contracts import (
    ExtensionCallableId,
    ExtensionCallableSpec,
    ExtensionImplementationRef,
)
from .extension_interface import normalize_interface_module


_SNAPSHOT_FINGERPRINT_SCHEMA = "nodeforge-extension-owner-code-v1"
_SYNTHETIC_ROOT = "_nodeforge_ext"


def system_owner_key(record) -> tuple[str, ...]:
    """Return the canonical semantic owner key for one package system record."""
    return ("system", str(record.package_id), str(record.system_id))


def library_owner_key(record) -> tuple[str, ...]:
    """Return the canonical semantic owner key for one native library record."""
    return (
        "library",
        str(record.namespace),
        normalize_library_package_id(getattr(record, "package_id", "")),
        str(record.name),
    )


def _framed_hash_parts(parts: Iterable[bytes]) -> bytes:
    """Return unambiguously length-framed bytes for deterministic hashing."""
    out = bytearray()
    for part in parts:
        out.extend(len(part).to_bytes(8, "big", signed=False))
        out.extend(part)
    return bytes(out)


def _owner_digest(owner_key: tuple[str, ...]) -> str:
    """Return the stable synthetic-module digest for one canonical owner tuple."""
    framed = _framed_hash_parts(str(part).encode("utf-8") for part in owner_key)
    return hashlib.sha256(framed).hexdigest()


def synthetic_owner_module_base(owner_key: tuple[str, ...]) -> str:
    """Return the stable import-safe synthetic module namespace for an owner."""
    return f"{_SYNTHETIC_ROOT}.{_owner_digest(tuple(owner_key))}"


@dataclass(frozen=True)
class _CapturedModule:
    """Describe one captured source module within an extension owner snapshot."""

    relative_path: str
    source: bytes
    is_package: bool


@dataclass(frozen=True)
class OwnerCodeSnapshot:
    """Store one immutable conservative owner-local Python source snapshot."""

    owner_key: tuple[str, ...]
    modules: Mapping[str, _CapturedModule]
    namespace_packages: frozenset[str]
    fingerprint: str

    def __post_init__(self) -> None:
        """Freeze captured mappings and validate the required interface module."""
        object.__setattr__(self, "owner_key", tuple(self.owner_key))
        object.__setattr__(self, "modules", MappingProxyType(dict(self.modules)))
        object.__setattr__(self, "namespace_packages", frozenset(self.namespace_packages))
        if "interface" not in self.modules:
            raise CompileError("Extension owner snapshot is missing interface.py")

    @property
    def module_base(self) -> str:
        """Return the stable synthetic module namespace for this owner."""
        return synthetic_owner_module_base(self.owner_key)

    def module_exists(self, relative_module: str) -> bool:
        """Return whether one normalized ``.module`` resolves to captured source."""
        if not isinstance(relative_module, str) or not relative_module.startswith("."):
            return False
        return relative_module[1:] in self.modules


def _snapshot_fingerprint(owner_key: tuple[str, ...], entries: Mapping[str, bytes]) -> str:
    """Hash the exact canonical owner key, logical paths, and captured source bytes."""
    parts: list[bytes] = [_SNAPSHOT_FINGERPRINT_SCHEMA.encode("utf-8")]
    parts.extend(str(part).encode("utf-8") for part in owner_key)
    for path in sorted(entries):
        parts.append(path.encode("utf-8"))
        parts.append(entries[path])
    return hashlib.sha256(_framed_hash_parts(parts)).hexdigest()


def capture_owner_code_snapshot(owner_key: tuple[str, ...], root: Path) -> OwnerCodeSnapshot:
    """Capture every supported owner-local Python source file before owner execution."""
    owner_key = tuple(owner_key)
    root = Path(root).resolve()
    interface_path = root / "interface.py"
    if not interface_path.is_file() or interface_path.is_symlink():
        raise CompileError(f"Extension owner {owner_key!r} must contain a regular interface.py")

    entries: dict[str, bytes] = {}
    modules: dict[str, _CapturedModule] = {}
    namespace_packages: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(dirpath)
        rel_dir = directory.relative_to(root)
        dirnames[:] = [
            name
            for name in dirnames
            if name != "__pycache__" and not name.startswith(".")
        ]
        for filename in sorted(filenames):
            if not filename.endswith(".py") or filename.startswith("."):
                continue
            path = directory / filename
            relative = path.relative_to(root)
            if relative.as_posix() == "semantic.py":
                continue
            if any(part == "__pycache__" or part.startswith(".") for part in relative.parts):
                continue
            if path.is_symlink():
                raise CompileError(f"Extension owner contains symlinked Python source: {relative.as_posix()}")
            resolved = path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise CompileError(f"Extension Python source escapes owner root: {relative.as_posix()}") from exc
            try:
                source = path.read_bytes()
            except OSError as exc:
                raise CompileError(f"Could not read extension Python source {relative.as_posix()}: {exc}") from exc
            rel_text = relative.as_posix()
            entries[rel_text] = source

            if filename == "__init__.py":
                if not rel_dir.parts:
                    # The owner root is a synthetic namespace. Root __init__.py remains
                    # fingerprinted but is not a protocol entry point in this plan.
                    continue
                logical = ".".join(rel_dir.parts)
                is_package = True
            else:
                logical_parts = (*rel_dir.parts, path.stem)
                logical = ".".join(logical_parts)
                is_package = False
            if logical in modules:
                raise CompileError(f"Extension owner has ambiguous logical Python module {logical!r}")
            modules[logical] = _CapturedModule(rel_text, source, is_package)
    # Directories containing child modules behave as namespace packages when no
    # captured __init__.py provides the package module itself.
    for logical in tuple(modules):
        parts = logical.split(".")
        for index in range(1, len(parts)):
            prefix = ".".join(parts[:index])
            if prefix not in modules:
                namespace_packages.add(prefix)

    fingerprint = _snapshot_fingerprint(owner_key, entries)
    return OwnerCodeSnapshot(owner_key, modules, frozenset(namespace_packages), fingerprint)


class _SnapshotLoader(importlib.abc.Loader):
    """Execute captured source bytes for one owner/session logical module."""

    def __init__(self, session: "ExtensionOwnerSession", logical_name: str, *, namespace: bool = False):
        """Bind one import loader to a fresh owner session and logical module."""
        self.session = session
        self.logical_name = logical_name
        self.namespace = namespace

    def create_module(self, spec):
        """Use Python's default module construction."""
        return None

    def exec_module(self, module) -> None:
        """Execute one captured module or initialize one synthetic namespace package."""
        module.__package__ = module.__name__ if self.namespace else module.__spec__.parent
        if self.namespace:
            module.__path__ = []
            self.session.modules[self.logical_name] = module
            return
        captured = self.session.snapshot.modules[self.logical_name]
        module.__file__ = f"<nodeforge-extension:{captured.relative_path}>"
        if captured.is_package:
            module.__path__ = []
            module.__package__ = module.__name__
        try:
            code = compile(captured.source, module.__file__, "exec")
            exec(code, module.__dict__)
        except CompileError:
            raise
        except Exception as exc:
            raise CompileError(
                f"Could not execute extension module {captured.relative_path}: {exc}"
            ) from exc
        self.session.modules[self.logical_name] = module


class _SnapshotFinder(importlib.abc.MetaPathFinder):
    """Resolve synthetic owner-local imports exclusively from one captured snapshot."""

    def __init__(self, session: "ExtensionOwnerSession"):
        """Bind one meta-path finder to the owner session it may resolve."""
        self.session = session

    def find_spec(self, fullname, path=None, target=None):
        """Return a captured/namespace module spec within this owner namespace."""
        base = self.session.snapshot.module_base
        prefix = base + "."
        if not fullname.startswith(prefix):
            return None
        logical = fullname[len(prefix):]
        captured = self.session.snapshot.modules.get(logical)
        if captured is not None:
            return importlib.util.spec_from_loader(
                fullname,
                _SnapshotLoader(self.session, logical),
                is_package=captured.is_package,
            )
        if logical in self.session.snapshot.namespace_packages:
            return importlib.util.spec_from_loader(
                fullname,
                _SnapshotLoader(self.session, logical, namespace=True),
                is_package=True,
            )
        return None


class ExtensionOwnerSession:
    """Own fresh Python module objects and normalized contracts for one owner snapshot."""

    def __init__(self, snapshot: OwnerCodeSnapshot):
        """Create a fresh module generation over one immutable owner snapshot."""
        if not isinstance(snapshot, OwnerCodeSnapshot):
            raise TypeError("snapshot must be OwnerCodeSnapshot")
        self.snapshot = snapshot
        self.modules: dict[str, object] = {}
        self._phase: str | None = None
        self._families: Mapping[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]] | None = None
        self._refs: Mapping[ExtensionCallableId, ExtensionImplementationRef] | None = None
        root_module = types.ModuleType(_SYNTHETIC_ROOT)
        root_module.__package__ = _SYNTHETIC_ROOT
        root_module.__path__ = []
        self._root_module = root_module
        base_module = types.ModuleType(snapshot.module_base)
        base_module.__package__ = snapshot.module_base
        base_module.__path__ = []
        self._base_module = base_module

    def _full_name(self, logical_name: str) -> str:
        """Return the stable synthetic fully-qualified name for one logical module."""
        return self.snapshot.module_base if not logical_name else f"{self.snapshot.module_base}.{logical_name}"

    @contextmanager
    def _mounted(self, phase: str):
        """Temporarily mount this session's synthetic namespace and snapshot finder."""
        if self._phase is not None:
            raise CompileError("Internal error: extension owner session phase is already active")
        previous_phase = self._phase
        self._phase = phase
        finder = _SnapshotFinder(self)
        names = {
            _SYNTHETIC_ROOT: self._root_module,
            self.snapshot.module_base: self._base_module,
        }
        names.update({self._full_name(logical): module for logical, module in self.modules.items()})
        prefix = self.snapshot.module_base + "."
        previous: dict[str, object] = {
            name: module
            for name, module in list(sys.modules.items())
            if name == _SYNTHETIC_ROOT or name == self.snapshot.module_base or name.startswith(prefix)
        }
        try:
            # Stable synthetic names are reused across registry generations. Remove
            # every pre-existing owner-scoped module before mounting this session so
            # importlib can never satisfy an owner-local import from stale globals.
            for name in tuple(previous):
                sys.modules.pop(name, None)
            for name, module in names.items():
                sys.modules[name] = module
            sys.meta_path.insert(0, finder)
            yield
        finally:
            try:
                sys.meta_path.remove(finder)
            except ValueError:
                pass
            current_names = {
                name
                for name in list(sys.modules)
                if name == _SYNTHETIC_ROOT or name == self.snapshot.module_base or name.startswith(prefix)
            }
            for name in current_names:
                sys.modules.pop(name, None)
            for name, module in previous.items():
                sys.modules[name] = module
            self._phase = previous_phase

    def normalize_interface(self) -> tuple[
        Mapping[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]],
        Mapping[ExtensionCallableId, ExtensionImplementationRef],
    ]:
        """Execute and normalize ``interface.py`` exactly once for this session."""
        if self._families is not None and self._refs is not None:
            return self._families, self._refs
        with self._mounted("INTERFACE"):
            module = importlib.import_module(self._full_name("interface"))
        families, refs = normalize_interface_module(
            module,
            owner_key=self.snapshot.owner_key,
            module_exists=self.snapshot.module_exists,
        )
        for ref in refs.values():
            logical = ref.module[1:]
            if logical != "interface" and logical in self.modules:
                raise CompileError(
                    f"Extension implementation module {ref.module!r} executed during interface normalization"
                )
        self._families = MappingProxyType(dict(families))
        self._refs = MappingProxyType(dict(refs))
        return self._families, self._refs

    def resolve_implementation(self, callable_id: ExtensionCallableId):
        """Lazily resolve one physical callable from captured owner-local source."""
        families, refs = self.normalize_interface()
        if callable_id not in families:
            raise CompileError(f"Unknown extension callable identity: {callable_id}")
        ref = refs[callable_id]
        logical = ref.module[1:]
        with self._mounted("IMPLEMENTATION"):
            module = importlib.import_module(self._full_name(logical))
            try:
                implementation = getattr(module, ref.attribute)
            except AttributeError as exc:
                raise CompileError(
                    f"Extension implementation {ref.module}:{ref.attribute} does not exist"
                ) from exc
        if not callable(implementation):
            raise CompileError(f"Extension implementation {ref.module}:{ref.attribute} is not callable")
        return implementation


class ExtensionRegistry:
    """Immutable normalized extension families backed by coherent owner sessions."""

    def __init__(self, sessions: Iterable[ExtensionOwnerSession] = ()):
        """Freeze normalized owner sessions into one compilation registry."""
        owner_sessions: dict[tuple[str, ...], ExtensionOwnerSession] = {}
        families: dict[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]] = {}
        for session in sessions:
            if not isinstance(session, ExtensionOwnerSession):
                raise TypeError("ExtensionRegistry sessions must be ExtensionOwnerSession records")
            owner_key = session.snapshot.owner_key
            if owner_key in owner_sessions:
                raise CompileError(f"Duplicate extension owner snapshot: {owner_key!r}")
            owner_sessions[owner_key] = session
            owner_families, _ = session.normalize_interface()
            for callable_id, specs in owner_families.items():
                if callable_id in families:
                    raise CompileError(f"Duplicate extension callable identity: {callable_id}")
                families[callable_id] = tuple(specs)
        self._sessions = MappingProxyType(owner_sessions)
        self._families = MappingProxyType(families)

    @classmethod
    def empty(cls) -> "ExtensionRegistry":
        """Return an empty immutable registry for environments without v2 owners."""
        return cls(())

    def callable_specs(self, callable_id: ExtensionCallableId) -> tuple[ExtensionCallableSpec, ...]:
        """Return the frozen ordered alternatives for one public callable family."""
        try:
            return self._families[callable_id]
        except KeyError as exc:
            raise CompileError(f"Unknown extension callable identity: {callable_id}") from exc

    def owner_fingerprint(self, owner_key: tuple[str, ...]) -> str:
        """Return the conservative owner-code fingerprint for one admitted owner."""
        try:
            return self._sessions[tuple(owner_key)].snapshot.fingerprint
        except KeyError as exc:
            raise CompileError(f"Unknown extension owner identity: {tuple(owner_key)!r}") from exc

    def resolve_implementation(self, callable_id: ExtensionCallableId):
        """Resolve one callable through the same owner session used for declaration normalization."""
        try:
            session = self._sessions[callable_id.owner]
        except KeyError as exc:
            raise CompileError(f"Unknown extension owner identity: {callable_id.owner!r}") from exc
        return session.resolve_implementation(callable_id)

    def contains(self, callable_id: ExtensionCallableId) -> bool:
        """Return whether one canonical callable identity belongs to this registry."""
        return callable_id in self._families


__all__ = [
    "ExtensionOwnerSession",
    "ExtensionRegistry",
    "OwnerCodeSnapshot",
    "capture_owner_code_snapshot",
    "library_owner_key",
    "synthetic_owner_module_base",
    "system_owner_key",
]
