"""Immutable package, catalog, and system selections for one compilation session."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Never, TYPE_CHECKING

from .errors import CompileError
from . import packages
from .systems import registry as systems_registry

if TYPE_CHECKING:
    from .library import LibraryEntryRecord


_CATALOG_NAMES = frozenset({"functions", "examples", "local"})


@dataclass(frozen=True)
class ResolvedCompileErrorFailure:
    """Replay a deferred catalog CompileError without retaining traceback state."""

    exception_args: tuple[object, ...]

    def __post_init__(self) -> None:
        """Freeze the captured constructor arguments."""
        object.__setattr__(self, "exception_args", tuple(self.exception_args))

    def raise_error(self) -> Never:
        """Raise a fresh CompileError carrying the captured diagnostic."""
        raise CompileError(*self.exception_args)


@dataclass(frozen=True)
class ResolvedOSErrorFailure:
    """Replay a deferred filesystem error with its complete public diagnostic."""

    exception_type: type[OSError]
    exception_args: tuple[object, ...]
    errno: int | None
    strerror: str | None
    filename: object | None
    filename2: object | None
    winerror: int | None

    def __post_init__(self) -> None:
        """Validate the concrete error class and freeze constructor arguments."""
        if not isinstance(self.exception_type, type) or not issubclass(self.exception_type, OSError):
            raise TypeError("Resolved OSError failure requires an OSError subclass")
        object.__setattr__(self, "exception_args", tuple(self.exception_args))

    def raise_error(self) -> Never:
        """Raise a fresh filesystem error without adding absent path fields."""
        error = self.exception_type(*self.exception_args)
        error.errno = self.errno
        error.strerror = self.strerror
        if self.filename is not None:
            error.filename = self.filename
        if self.filename2 is not None:
            error.filename2 = self.filename2
        if self.winerror is not None:
            error.winerror = self.winerror
        raise error


ResolvedCatalogFailure = ResolvedCompileErrorFailure | ResolvedOSErrorFailure


@dataclass(frozen=True)
class ResolvedCatalog:
    """Immutable success or failure result for one public catalog namespace."""

    namespace: str
    entries: Mapping[str, "LibraryEntryRecord"]
    failure: ResolvedCatalogFailure | None = None

    def __post_init__(self) -> None:
        """Validate record placement and defensively freeze the entry mapping."""
        if self.namespace not in _CATALOG_NAMES:
            raise CompileError(f"Unknown library catalog: {self.namespace}")
        copied = dict(self.entries)
        if self.failure is not None and copied:
            raise ValueError("Failed resolved catalog cannot contain partial entries")
        for name, record in copied.items():
            if record.namespace != self.namespace or record.name != name:
                raise ValueError("Resolved catalog record does not match its namespace/name key")
        object.__setattr__(self, "entries", MappingProxyType(copied))

    def _raise_if_failed(self) -> None:
        """Replay the stored discovery failure before exposing catalog data."""
        if self.failure is not None:
            self.failure.raise_error()

    def names(self) -> frozenset[str]:
        """Return immutable public names or replay the stored discovery failure."""
        self._raise_if_failed()
        return frozenset(self.entries)

    def find(self, name: str) -> "LibraryEntryRecord | None":
        """Return one selected record or replay the stored discovery failure."""
        self._raise_if_failed()
        return self.entries.get(name)

    def records(self) -> tuple["LibraryEntryRecord", ...]:
        """Return selected records in deterministic public-name order."""
        self._raise_if_failed()
        return tuple(sorted(self.entries.values(), key=lambda record: record.name.lower()))


@dataclass(frozen=True)
class ResolvedEnvironment:
    """Immutable external callable selection shared by one root compilation."""

    catalogs: Mapping[str, ResolvedCatalog]
    system_constructors: Mapping[str, systems_registry.ResolvedSystemConstructor]

    def __post_init__(self) -> None:
        """Validate and defensively freeze catalogs and system bindings."""
        catalogs = dict(self.catalogs)
        if frozenset(catalogs) != _CATALOG_NAMES:
            raise ValueError("Resolved environment requires functions, examples, and local catalogs")
        for namespace, catalog in catalogs.items():
            if catalog.namespace != namespace:
                raise ValueError("Resolved environment catalog key does not match catalog namespace")
        systems = dict(self.system_constructors)
        for name, binding in systems.items():
            if binding.name != name:
                raise ValueError("Resolved system mapping key does not match constructor name")
        object.__setattr__(self, "catalogs", MappingProxyType(catalogs))
        object.__setattr__(self, "system_constructors", MappingProxyType(systems))

    def catalog(self, namespace: str) -> ResolvedCatalog:
        """Return one supported resolved catalog."""
        try:
            return self.catalogs[namespace]
        except KeyError as exc:
            raise CompileError(f"Unknown library catalog: {namespace}") from exc

    def system_names(self) -> tuple[str, ...]:
        """Return resolved constructor names in deterministic order."""
        return tuple(sorted(self.system_constructors))

    def system(self, name: str) -> systems_registry.ResolvedSystemConstructor | None:
        """Return the exact resolved owner for a constructor name."""
        return self.system_constructors.get(name)


def resolve_environment() -> ResolvedEnvironment:
    """Resolve active external callables once for a compilation session."""
    from . import library

    manifests = tuple(
        record.manifest
        for record in packages.active_package_records(include_invalid=False)
        if isinstance(record, packages.ActivePackage)
    )
    system_records = packages.system_package_records_from_manifests(manifests)
    systems = systems_registry.resolve_constructors(system_records)
    system_names = frozenset(systems)
    catalogs = {
        namespace: library.resolve_catalog(
            namespace,
            package_roots=packages.library_roots_from_manifests(namespace, manifests),
            system_constructor_names=system_names,
        )
        for namespace in ("functions", "examples", "local")
    }
    return ResolvedEnvironment(catalogs=catalogs, system_constructors=systems)


__all__ = [
    "ResolvedCatalog",
    "ResolvedCatalogFailure",
    "ResolvedCompileErrorFailure",
    "ResolvedEnvironment",
    "ResolvedOSErrorFailure",
    "resolve_environment",
]
