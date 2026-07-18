"""Dynamic registry for package-backed embedded system constructor calls."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..errors import CompileError
from .. import packages

_RESERVED_CACHE: dict[str, "_ConstructorRecord"] | None = None
_MODULE_CACHE: dict[str, object] = {}


@dataclass(frozen=True)
class _ConstructorRecord:
    """Resolved owner for one public system constructor name."""

    record: packages.SystemPackageRecord
    constructors: tuple[str, ...]


def invalidate_cache() -> None:
    """Clear dynamic constructor and synthetic module caches."""
    global _RESERVED_CACHE
    _RESERVED_CACHE = None
    # Synthetic package names contain install-path hashes; old modules are harmless but
    # should not be reused by this registry after package state changes.
    _MODULE_CACHE.clear()


def constructor_names() -> tuple[str, ...]:
    """Return the active package-backed constructor names."""
    return tuple(sorted(_constructor_map()))


def has_system_constructor(name: str) -> bool:
    """Return True when *name* is reserved for a package-backed constructor."""
    return name in _constructor_map()


def constructor_owner(name: str) -> packages.SystemPackageRecord | None:
    """Return the package system record that owns *name*, if active."""
    item = _constructor_map().get(name)
    return item.record if item is not None else None


def get_handler(name: str) -> Callable:
    """Return the lazily loaded handler for an active package-backed constructor."""
    item = _constructor_map().get(name)
    if item is None:
        raise CompileError(f"Unsupported system constructor: {name}")
    handlers = _load_handlers(item)
    try:
        return handlers[name]
    except KeyError as exc:
        raise CompileError(f"System {item.record.system_id!r} did not provide handler for {name!r}") from exc


def compile_call(comp, expr, depth=0):
    """Compile one registered package-backed constructor call."""
    name = expr.func.id
    handler = get_handler(name)
    return handler(comp, expr, depth)


def clear_cache() -> None:
    """Clear dynamic constructor and synthetic module caches."""
    invalidate_cache()


def validate_no_reserved_collision(name: str, owner: str) -> None:
    """Reject user-owned callables that collide with active system names."""
    if name in constructor_names():
        raise CompileError(f"{owner} {name!r} collides with reserved system constructor name")


def _constructor_map() -> dict[str, _ConstructorRecord]:
    global _RESERVED_CACHE
    if _RESERVED_CACHE is not None:
        return _RESERVED_CACHE
    out: dict[str, _ConstructorRecord] = {}
    owners: dict[str, str] = {}
    for record in packages.system_package_records():
        module = _load_system_entrypoint(record)
        constructors = _read_constructors(module, record)
        constructor_record = _ConstructorRecord(record, constructors)
        for name in constructors:
            if name in out:
                raise CompileError(
                    f"Duplicate system constructor {name!r}: {owners[name]} and {record.package_id}/{record.system_id}"
                )
            out[name] = constructor_record
            owners[name] = f"{record.package_id}/{record.system_id}"
    _RESERVED_CACHE = out
    return out


def _read_constructors(module, record: packages.SystemPackageRecord) -> tuple[str, ...]:
    raw = getattr(module, "CONSTRUCTORS", None)
    if not isinstance(raw, (list, tuple)) or not raw:
        raise CompileError(f"System {record.package_id}/{record.system_id} must declare CONSTRUCTORS")
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item or not item.isidentifier() or item.startswith("_"):
            raise CompileError(f"System {record.package_id}/{record.system_id} has invalid constructor name {item!r}")
        if item in seen:
            raise CompileError(f"System {record.package_id}/{record.system_id} declares duplicate constructor {item!r}")
        seen.add(item)
        names.append(item)
    if not callable(getattr(module, "load_handlers", None)):
        raise CompileError(f"System {record.package_id}/{record.system_id} must define load_handlers()")
    return tuple(names)


def _load_handlers(owner: _ConstructorRecord) -> dict[str, Callable]:
    module = _load_system_entrypoint(owner.record)
    handlers = module.load_handlers()
    if not isinstance(handlers, dict):
        raise CompileError(f"System {owner.record.package_id}/{owner.record.system_id} load_handlers() must return a dict")
    expected = set(owner.constructors)
    actual = set(handlers)
    if actual != expected:
        raise CompileError(
            f"System {owner.record.package_id}/{owner.record.system_id} handlers do not match CONSTRUCTORS"
        )
    for name, handler in handlers.items():
        if not callable(handler):
            raise CompileError(f"System handler {name!r} from {owner.record.package_id}/{owner.record.system_id} is not callable")
    return handlers


def _load_system_entrypoint(record: packages.SystemPackageRecord):
    base_pkg = _synthetic_base_package(record)
    module_name = f"{base_pkg}.system"
    cached = _MODULE_CACHE.get(module_name)
    if cached is not None:
        return cached
    _ensure_synthetic_package(base_pkg, record.root)
    spec = importlib.util.spec_from_file_location(module_name, record.module_path)
    if spec is None or spec.loader is None:
        raise CompileError(f"Could not load system entrypoint: {record.module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _MODULE_CACHE[module_name] = module
    return module


def _synthetic_base_package(record: packages.SystemPackageRecord) -> str:
    package = __package__.split(".systems")[0] if __package__ else "NodeForge"
    safe_package_id = "".join(ch if ch.isalnum() else "_" for ch in record.package_id)
    safe_system_id = "".join(ch if ch.isalnum() else "_" for ch in record.system_id)
    digest = hashlib.sha256(str(record.root).encode("utf-8")).hexdigest()[:12]
    return f"{package}._package_modules.{safe_package_id}.systems.{safe_system_id}_{digest}"


def _ensure_synthetic_package(base_pkg: str, system_root: Path) -> None:
    parts = base_pkg.split(".")
    for index in range(1, len(parts) + 1):
        name = ".".join(parts[:index])
        if name in sys.modules:
            module = sys.modules[name]
        else:
            module = types.ModuleType(name)
            module.__package__ = name
            module.__path__ = []
            sys.modules[name] = module
        if index == len(parts):
            module.__path__ = [str(system_root)]
            module.__package__ = name


def NAMES() -> tuple[str, ...]:
    """Compatibility callable returning the active constructor set."""
    return constructor_names()


__all__ = [
    "constructor_names",
    "constructor_owner",
    "get_handler",
    "clear_cache",
    "has_system_constructor",
    "compile_call",
    "validate_no_reserved_collision",
    "invalidate_cache",
    "NAMES",
]
