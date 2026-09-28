"""Structural public-name view for installed Extension API v2 system owners."""

from __future__ import annotations

from ..errors import CompileError
from .. import packages

_RESERVED_CACHE: dict[str, packages.SystemPackageRecord] | None = None


def invalidate_cache() -> None:
    """Clear the live convenience cache of v2 system callable names."""
    global _RESERVED_CACHE
    _RESERVED_CACHE = None


def clear_cache() -> None:
    """Compatibility alias for invalidating structural system-name state."""
    invalidate_cache()


def _constructor_map() -> dict[str, packages.SystemPackageRecord]:
    """Return live v2 callable names without loading any v1 system handler module."""
    global _RESERVED_CACHE
    if _RESERVED_CACHE is not None:
        return _RESERVED_CACHE

    from ..extension_registry import (
        ExtensionOwnerSession,
        capture_owner_code_snapshot,
        system_owner_key,
    )

    out: dict[str, packages.SystemPackageRecord] = {}
    owners: dict[str, str] = {}
    for record in packages.system_package_records():
        if record.interface_path is None:
            raise CompileError(
                f"System {record.package_id}/{record.system_id} uses unsupported Extension API v1 system.py"
            )
        session = ExtensionOwnerSession(capture_owner_code_snapshot(system_owner_key(record), record.root))
        families, _refs = session.normalize_interface()
        for callable_id in families:
            name = callable_id.name
            if name in out:
                raise CompileError(
                    f"Duplicate system constructor {name!r}: {owners[name]} and "
                    f"{record.package_id}/{record.system_id}"
                )
            out[name] = record
            owners[name] = f"{record.package_id}/{record.system_id}"
    _RESERVED_CACHE = out
    return out


def constructor_names() -> tuple[str, ...]:
    """Return active Extension API v2 system callable names."""
    return tuple(sorted(_constructor_map()))


def has_system_constructor(name: str) -> bool:
    """Return whether an active v2 system owner declares *name*."""
    return name in _constructor_map()


def constructor_owner(name: str) -> packages.SystemPackageRecord | None:
    """Return the v2 system record that owns *name*, if active."""
    return _constructor_map().get(name)


def validate_no_reserved_collision(name: str, owner: str) -> None:
    """Reject user-owned callables that collide with active v2 system names."""
    if name in constructor_names():
        raise CompileError(f"{owner} {name!r} collides with reserved system constructor name")


def NAMES() -> tuple[str, ...]:
    """Return the active constructor set for legacy discovery callers."""
    return constructor_names()


__all__ = [
    "constructor_names",
    "constructor_owner",
    "clear_cache",
    "has_system_constructor",
    "validate_no_reserved_collision",
    "invalidate_cache",
    "NAMES",
]
