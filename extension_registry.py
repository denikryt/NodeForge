"""Snapshot-backed owner sessions and immutable declarative extension registry."""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.util
import inspect
import os
import sys
import types
import typing
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
    ExtensionTypeId,
    ExtensionTypeSpec,
    TypeSpec,
)
from .extension_interface import (
    normalize_interface_module,
    normalize_interface_records,
    normalize_semantic_type_annotation,
)


_SNAPSHOT_FINGERPRINT_SCHEMA = "nodeforge-extension-owner-code-v1"
_SYNTHETIC_ROOT = "_nodeforge_ext"
_PHASES = frozenset({"INTERFACE", "SEMANTIC", "IMPLEMENTATION"})
_PHASE_MODULE_PHASES = ("SEMANTIC", "IMPLEMENTATION")
_ABSENT = object()


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
        dirnames[:] = [name for name in dirnames if name != "__pycache__" and not name.startswith(".")]
        for filename in sorted(filenames):
            if not filename.endswith(".py") or filename.startswith("."):
                continue
            path = directory / filename
            relative = path.relative_to(root)
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
                    continue
                logical = ".".join(rel_dir.parts)
                is_package = True
            else:
                logical = ".".join((*rel_dir.parts, path.stem))
                is_package = False
            if logical in modules:
                raise CompileError(f"Extension owner has ambiguous logical Python module {logical!r}")
            modules[logical] = _CapturedModule(rel_text, source, is_package)
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
            self.session._store_loaded_module(self.logical_name, module)
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
            raise CompileError(f"Could not execute extension module {captured.relative_path}: {exc}") from exc
        self.session._store_loaded_module(self.logical_name, module)


class _SnapshotFinder(importlib.abc.MetaPathFinder):
    """Resolve synthetic owner-local imports exclusively from one captured snapshot generation."""

    def __init__(self, session: "ExtensionOwnerSession"):
        """Bind one meta-path finder to the owner session it may resolve."""
        self.session = session

    def find_spec(self, fullname, path=None, target=None):
        """Return a phase-legal captured/namespace module spec within this owner namespace."""
        base = self.session.snapshot.module_base
        prefix = base + "."
        if not fullname.startswith(prefix):
            return None
        logical = fullname[len(prefix):]
        self.session._require_module_allowed(logical)
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


@dataclass(frozen=True)
class _SemanticCallableContract:
    """Cache one same-name semantic implementation and its normalized return schema."""

    function: object
    return_type: TypeSpec


class ExtensionOwnerSession:
    """Own phase-isolated Python module generations and normalized contracts for one owner."""

    def __init__(self, snapshot: OwnerCodeSnapshot):
        """Create a fresh module/session generation over one immutable owner snapshot."""
        if not isinstance(snapshot, OwnerCodeSnapshot):
            raise TypeError("snapshot must be OwnerCodeSnapshot")
        self.snapshot = snapshot
        self._phase: str | None = None
        self._canonical_modules: dict[str, object] = {}
        self._phase_modules: dict[str, dict[str, object]] = {phase: {} for phase in _PHASE_MODULE_PHASES}
        self._families: Mapping[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]] | None = None
        self._refs: Mapping[ExtensionCallableId, ExtensionImplementationRef | None] | None = None
        self._type_specs: Mapping[ExtensionTypeId, ExtensionTypeSpec] = MappingProxyType({})
        self._classes_by_type: Mapping[ExtensionTypeId, type] = MappingProxyType({})
        self._type_ids_by_class: Mapping[type, ExtensionTypeId] = MappingProxyType({})
        self._implementation_modules: frozenset[str] = frozenset()
        self._semantic_contracts: dict[ExtensionCallableId, object] = {}
        root_module = types.ModuleType(_SYNTHETIC_ROOT)
        root_module.__package__ = _SYNTHETIC_ROOT
        root_module.__path__ = []
        self._root_module = root_module
        base_module = types.ModuleType(snapshot.module_base)
        base_module.__package__ = snapshot.module_base
        base_module.__path__ = []
        self._base_module = base_module

    @property
    def modules(self) -> dict[str, object]:
        """Return a diagnostic merged view without changing phase ownership semantics."""
        merged = dict(self._canonical_modules)
        for phase in _PHASE_MODULE_PHASES:
            merged.update(self._phase_modules[phase])
        return merged

    def _full_name(self, logical_name: str) -> str:
        """Return the stable synthetic fully-qualified name for one logical module."""
        return self.snapshot.module_base if not logical_name else f"{self.snapshot.module_base}.{logical_name}"

    def _store_loaded_module(self, logical_name: str, module: object) -> None:
        """Store a snapshot-loaded module in the canonical or active phase generation."""
        if self._phase is None:
            raise CompileError("Internal error: extension module loaded outside an owner-session phase")
        if logical_name == "interface":
            existing = self._canonical_modules.get("interface")
            if existing is not None and existing is not module:
                raise CompileError("Internal error: canonical extension interface module identity changed")
            self._canonical_modules["interface"] = module
            return
        if self._phase == "INTERFACE":
            raise CompileError(
                "Internal error: declaration-only interface phase attempted to retain an owner-local child module"
            )
        self._phase_modules[self._phase][logical_name] = module

    def _require_module_allowed(self, logical_name: str) -> None:
        """Reject owner-local imports that cross the active lifecycle phase boundary."""
        if self._phase not in _PHASES:
            raise CompileError("Internal error: owner-local import occurred outside an extension phase")
        if logical_name == "interface":
            return
        if self._phase == "INTERFACE":
            raise CompileError(
                f"interface.py is declaration-only and cannot import owner-local module '.{logical_name}'"
            )
        if logical_name == "semantic":
            if self._phase != "SEMANTIC":
                raise CompileError(f"semantic.py is unavailable during extension {self._phase} phase")
            return
        if self._phase == "SEMANTIC" and logical_name in self._implementation_modules:
            raise CompileError(
                f"Physical extension implementation module '.{logical_name}' is unavailable during SEMANTIC phase"
            )

    def _clear_canonical_child_attributes(self) -> None:
        """Remove phase-owned child module references from canonical package containers."""
        base_prefix = self.snapshot.module_base + "."
        for name, value in list(vars(self._base_module).items()):
            if name == "interface":
                continue
            if isinstance(value, types.ModuleType) and getattr(value, "__name__", "").startswith(base_prefix):
                try:
                    delattr(self._base_module, name)
                except AttributeError:
                    pass
        # The owner base itself is the only intended child under the synthetic root.
        digest_name = self.snapshot.module_base.rsplit(".", 1)[-1]
        for name, value in list(vars(self._root_module).items()):
            if name == digest_name and value is self._base_module:
                continue
            if isinstance(value, types.ModuleType) and getattr(value, "__name__", "").startswith(self.snapshot.module_base):
                try:
                    delattr(self._root_module, name)
                except AttributeError:
                    pass

    def _restore_phase_parent_attributes(self, cache: Mapping[str, object]) -> None:
        """Restore importlib-style parent attributes for one already-loaded phase generation."""
        digest_name = self.snapshot.module_base.rsplit(".", 1)[-1]
        setattr(self._root_module, digest_name, self._base_module)
        interface = self._canonical_modules.get("interface")
        if interface is not None:
            setattr(self._base_module, "interface", interface)
        all_modules = {**self._canonical_modules, **cache}
        for logical, module in sorted(cache.items(), key=lambda item: item[0].count(".")):
            if "." in logical:
                parent_logical, child = logical.rsplit(".", 1)
                parent = all_modules.get(parent_logical)
            else:
                parent, child = self._base_module, logical
            if parent is not None and isinstance(parent, types.ModuleType):
                setattr(parent, child, module)

    @contextmanager
    def _mounted(self, phase: str):
        """Temporarily mount exactly one phase-local owner generation and snapshot finder."""
        if phase not in _PHASES:
            raise ValueError(f"unknown extension owner phase: {phase}")
        if self._phase is not None:
            raise CompileError("Internal error: extension owner session phase is already active")
        self._phase = phase
        finder = _SnapshotFinder(self)
        cache = self._phase_modules.get(phase, {})
        names = {
            _SYNTHETIC_ROOT: self._root_module,
            self.snapshot.module_base: self._base_module,
        }
        if "interface" in self._canonical_modules:
            names[self._full_name("interface")] = self._canonical_modules["interface"]
        names.update({self._full_name(logical): module for logical, module in cache.items()})
        prefix = self.snapshot.module_base + "."
        previous = {
            name: module
            for name, module in list(sys.modules.items())
            if name == _SYNTHETIC_ROOT or name == self.snapshot.module_base or name.startswith(prefix)
        }
        try:
            for name in tuple(previous):
                sys.modules.pop(name, None)
            self._clear_canonical_child_attributes()
            for name, module in names.items():
                sys.modules[name] = module
            self._restore_phase_parent_attributes(cache)
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
            self._clear_canonical_child_attributes()
            for name, module in previous.items():
                sys.modules[name] = module
            self._phase = None

    def normalize_interface(self) -> tuple[
        Mapping[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]],
        Mapping[ExtensionCallableId, ExtensionImplementationRef | None],
    ]:
        """Execute and normalize ``interface.py`` exactly once for this session."""
        if self._families is not None and self._refs is not None:
            return self._families, self._refs
        with self._mounted("INTERFACE"):
            module = importlib.import_module(self._full_name("interface"))
        type_specs, classes_by_type, type_ids_by_class = normalize_interface_records(
            module,
            owner_key=self.snapshot.owner_key,
        )
        families, refs = normalize_interface_module(
            module,
            owner_key=self.snapshot.owner_key,
            module_exists=self.snapshot.module_exists,
            class_type_ids=type_ids_by_class,
            record_type_specs=type_specs,
        )
        implementation_modules = frozenset(
            ref.module[1:] for ref in refs.values() if ref is not None
        )
        self._families = MappingProxyType(dict(families))
        self._refs = MappingProxyType(dict(refs))
        self._type_specs = MappingProxyType(dict(type_specs))
        self._classes_by_type = MappingProxyType(dict(classes_by_type))
        self._type_ids_by_class = MappingProxyType(dict(type_ids_by_class))
        self._implementation_modules = implementation_modules
        return self._families, self._refs

    def type_specs(self) -> Mapping[ExtensionTypeId, ExtensionTypeSpec]:
        """Return the immutable normalized semantic record schemas for this owner."""
        self.normalize_interface()
        return self._type_specs

    def type_spec(self, type_id: ExtensionTypeId) -> ExtensionTypeSpec:
        """Return one normalized semantic record schema owned by this session."""
        self.normalize_interface()
        try:
            return self._type_specs[type_id]
        except KeyError as exc:
            raise CompileError(f"Unknown extension semantic type: {type_id}") from exc

    def python_class_for(self, type_id: ExtensionTypeId) -> type:
        """Return the exact current-session Python dataclass for one semantic type."""
        self.normalize_interface()
        try:
            return self._classes_by_type[type_id]
        except KeyError as exc:
            raise CompileError(f"Unknown extension semantic type: {type_id}") from exc

    def type_id_for_exact_class(self, cls: type) -> ExtensionTypeId:
        """Return the semantic ID for one exact registered current-session class."""
        self.normalize_interface()
        try:
            return self._type_ids_by_class[cls]
        except KeyError as exc:
            raise CompileError("Package semantic record instance does not use a registered current-session class") from exc

    def type_spec_for_instance(self, value: object) -> ExtensionTypeSpec:
        """Return the concrete registered schema for one exact current-session instance."""
        return self.type_spec(self.type_id_for_exact_class(type(value)))

    def is_nominal_subtype(self, concrete: ExtensionTypeId, expected: ExtensionTypeId) -> bool:
        """Return whether one same-owner semantic record type is the expected type or a normalized subtype."""
        self.normalize_interface()
        if concrete.owner != expected.owner or concrete.owner != self.snapshot.owner_key:
            return False
        pending = [concrete]
        visited: set[ExtensionTypeId] = set()
        while pending:
            current = pending.pop()
            if current == expected:
                return True
            if current in visited:
                continue
            visited.add(current)
            spec = self._type_specs.get(current)
            if spec is not None:
                pending.extend(spec.bases)
        return False

    def implementation_ref(self, callable_id: ExtensionCallableId) -> ExtensionImplementationRef | None:
        """Return the optional normalized physical implementation reference."""
        families, refs = self.normalize_interface()
        if callable_id not in families:
            raise CompileError(f"Unknown extension callable identity: {callable_id}")
        return refs[callable_id]

    def has_implementation(self, callable_id: ExtensionCallableId) -> bool:
        """Return whether one declared callable owns a physical implementation target."""
        return self.implementation_ref(callable_id) is not None

    def _resolve_implementation_mounted(self, callable_id: ExtensionCallableId):
        """Resolve one physical callable while the IMPLEMENTATION generation is mounted."""
        if self._phase != "IMPLEMENTATION":
            raise CompileError("Internal error: physical implementation resolution requires IMPLEMENTATION phase")
        ref = self.implementation_ref(callable_id)
        if ref is None:
            raise CompileError(f"Extension callable {callable_id.name!r} has no physical implementation")
        logical = ref.module[1:]
        module = importlib.import_module(self._full_name(logical))
        try:
            implementation = getattr(module, ref.attribute)
        except AttributeError as exc:
            raise CompileError(f"Extension implementation {ref.module}:{ref.attribute} does not exist") from exc
        if not callable(implementation):
            raise CompileError(f"Extension implementation {ref.module}:{ref.attribute} is not callable")
        return implementation

    def invoke_implementation(self, callable_id: ExtensionCallableId, *args, **kwargs):
        """Invoke one physical package function entirely inside the IMPLEMENTATION generation."""
        with self._mounted("IMPLEMENTATION"):
            implementation = self._resolve_implementation_mounted(callable_id)
            return implementation(*args, **kwargs)

    def _semantic_contract_mounted(self, callable_id: ExtensionCallableId) -> _SemanticCallableContract | None:
        """Resolve/cache one same-name semantic function while SEMANTIC is mounted."""
        cached = self._semantic_contracts.get(callable_id, None)
        if cached is _ABSENT:
            return None
        if isinstance(cached, _SemanticCallableContract):
            return cached
        self.normalize_interface()
        if "semantic" not in self.snapshot.modules:
            self._semantic_contracts[callable_id] = _ABSENT
            return None
        if self._phase != "SEMANTIC":
            raise CompileError("Internal error: semantic lookup requires SEMANTIC phase")
        module = importlib.import_module(self._full_name("semantic"))
        function = getattr(module, callable_id.name, None)
        if function is None:
            self._semantic_contracts[callable_id] = _ABSENT
            return None
        if not inspect.isfunction(function) or getattr(function, "__globals__", None) is not module.__dict__:
            raise CompileError(
                f"Extension semantic implementation {callable_id.name!r} must be a function defined in semantic.py"
            )
        signature = inspect.signature(function)
        try:
            annotations = typing.get_type_hints(
                function,
                globalns=function.__globals__,
                localns=function.__globals__,
                include_extras=True,
            )
        except Exception as exc:
            raise CompileError(
                f"Could not resolve semantic return annotation for {callable_id.name}(): {exc}"
            ) from exc
        return_annotation = annotations.get("return", signature.return_annotation)
        if return_annotation is inspect.Signature.empty:
            raise CompileError(f"semantic.py::{callable_id.name}() requires a return annotation")
        return_type = normalize_semantic_type_annotation(
            return_annotation,
            class_type_ids=self._type_ids_by_class,
            context=f"semantic.py::{callable_id.name}() return",
        )
        contract = _SemanticCallableContract(function, return_type)
        self._semantic_contracts[callable_id] = contract
        return contract

    def semantic_return_type(self, callable_id: ExtensionCallableId) -> TypeSpec | None:
        """Return the optional normalized same-name semantic implementation return type."""
        with self._mounted("SEMANTIC"):
            contract = self._semantic_contract_mounted(callable_id)
            return None if contract is None else contract.return_type

    def invoke_semantic(self, callable_id: ExtensionCallableId, *args, **kwargs):
        """Invoke one semantic package function entirely inside the SEMANTIC generation."""
        with self._mounted("SEMANTIC"):
            contract = self._semantic_contract_mounted(callable_id)
            if contract is None:
                raise CompileError(f"Extension callable {callable_id.name!r} has no semantic implementation")
            try:
                return contract.function(*args, **kwargs)
            except CompileError:
                raise
            except Exception as exc:
                raise CompileError(
                    f"Extension semantic implementation {callable_id.name!r} failed: {exc}"
                ) from exc


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

    def _session_for_owner(self, owner_key: tuple[str, ...]) -> ExtensionOwnerSession:
        """Return the unique current-session owner object for one canonical owner key."""
        try:
            return self._sessions[tuple(owner_key)]
        except KeyError as exc:
            raise CompileError(f"Unknown extension owner identity: {tuple(owner_key)!r}") from exc

    def callable_specs(self, callable_id: ExtensionCallableId) -> tuple[ExtensionCallableSpec, ...]:
        """Return the frozen ordered alternatives for one public callable family."""
        try:
            return self._families[callable_id]
        except KeyError as exc:
            raise CompileError(f"Unknown extension callable identity: {callable_id}") from exc

    def owner_fingerprint(self, owner_key: tuple[str, ...]) -> str:
        """Return the conservative owner-code fingerprint for one admitted owner."""
        return self._session_for_owner(owner_key).snapshot.fingerprint

    def invoke_implementation(self, callable_id: ExtensionCallableId, *args, **kwargs):
        """Invoke physical owner code while its IMPLEMENTATION generation remains mounted."""
        return self._session_for_owner(callable_id.owner).invoke_implementation(callable_id, *args, **kwargs)

    def has_implementation(self, callable_id: ExtensionCallableId) -> bool:
        """Return whether one callable owns a physical implementation reference."""
        return self._session_for_owner(callable_id.owner).has_implementation(callable_id)

    def semantic_return_type(self, callable_id: ExtensionCallableId) -> TypeSpec | None:
        """Return one optional same-name semantic implementation return contract."""
        return self._session_for_owner(callable_id.owner).semantic_return_type(callable_id)

    def invoke_semantic(self, callable_id: ExtensionCallableId, *args, **kwargs):
        """Invoke semantic owner code while its SEMANTIC generation remains mounted."""
        return self._session_for_owner(callable_id.owner).invoke_semantic(callable_id, *args, **kwargs)

    def type_spec(self, type_id: ExtensionTypeId) -> ExtensionTypeSpec:
        """Return the canonical schema for one owner-scoped semantic record type."""
        return self._session_for_owner(type_id.owner).type_spec(type_id)

    def python_class_for(self, type_id: ExtensionTypeId) -> type:
        """Return the exact current-session package class for one semantic type."""
        return self._session_for_owner(type_id.owner).python_class_for(type_id)

    def type_spec_for_instance(
        self,
        value: object,
        *,
        owner: tuple[str, ...],
    ) -> ExtensionTypeSpec:
        """Return the exact-session semantic record schema for the expected owner."""
        return self._session_for_owner(owner).type_spec_for_instance(value)

    def is_nominal_subtype(self, concrete: ExtensionTypeId, expected: ExtensionTypeId) -> bool:
        """Return whether concrete is expected or one same-owner registered subtype."""
        if concrete.owner != expected.owner:
            return False
        return self._session_for_owner(concrete.owner).is_nominal_subtype(concrete, expected)

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
