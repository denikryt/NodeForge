"""Deterministic, validated physical owners under a package's Functions root.

This module describes source/native owners without importing package code.  Both
install validation and compilation consume the same physical owner positions.
"""

from __future__ import annotations

from dataclasses import dataclass
import keyword
from pathlib import Path
import re

from .errors import CompileError


_PUBLIC = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SOURCE_SUFFIXES = frozenset({".nf", ".nodeforge"})


@dataclass(frozen=True)
class PackageFunctionOwner:
    """One physical Functions owner, with directory/module import provenance."""

    name: str
    path: Path
    relative_parts: tuple[str, ...]
    kind: str
    source_path: Path | None = None
    interface_path: Path | None = None
    module_path: Path | None = None

    @property
    def folder_path(self) -> str:
        """Return the relative directory path excluding the owner itself."""
        return "/".join(self.relative_parts[:-1])

    @property
    def canonical_owner(self) -> str:
        """Return the dotted module/native owner prefix."""
        return ".".join(self.relative_parts)


def _public_component(name: str, location: Path) -> bool:
    """Validate one public component; skip private directories consistently."""
    if name.startswith(("_", ".")):
        return False
    if not _PUBLIC.fullmatch(name) or keyword.iskeyword(name):
        raise CompileError(f"Invalid package Functions namespace component {name!r} at {location}")
    return True


def scan_package_functions(root: Path) -> tuple[PackageFunctionOwner, ...]:
    """Return validated owners with deterministic recursion and safe boundaries.

    A legacy ``source.nf`` / native ``interface.py`` directory is an atomic
    owner. Its internal implementation files do not become nested exports.
    Other directories are namespaces, including when a same-stem .nf module
    exists alongside a directory at the same level.
    """
    root = Path(root)
    if not root.is_dir():
        raise CompileError(f"Package Functions root is not a directory: {root}")
    boundary = root.resolve()
    owners: list[PackageFunctionOwner] = []

    def safe(path: Path) -> None:
        """Reject symlinks and paths outside this declared content root."""
        if path.is_symlink() or not path.resolve().is_relative_to(boundary):
            raise CompileError(f"Package Functions owner escapes its declared root or is a symlink: {path}")

    def walk(directory: Path, prefix: tuple[str, ...]) -> None:
        """Scan a namespace, treating legacy/native owner directories as leaves."""
        safe(directory)
        children = sorted(directory.iterdir(), key=lambda p: p.name)
        for child in children:
            if child.is_symlink():
                # Reject public symlinks rather than silently dropping a callable.
                if not child.name.startswith(("_", ".")):
                    raise CompileError(f"Package Functions symlink is not supported: {child}")
                continue
            if child.is_file() and child.suffix in _SOURCE_SUFFIXES:
                if child.name == "source.nf" and prefix:
                    # Legacy owner's source is handled with its enclosing folder.
                    continue
                if not _public_component(child.stem, child):
                    continue
                safe(child)
                owners.append(PackageFunctionOwner(
                    child.stem, child, prefix + (child.stem,), "script", source_path=child,
                ))
            elif child.is_dir():
                if not _public_component(child.name, child):
                    continue
                safe(child)
                relative = prefix + (child.name,)
                source = child / "source.nf"
                interface = child / "interface.py"
                legacy = child / "function.py"
                if source.exists() and interface.exists():
                    raise CompileError(f"Unsupported mixed source.nf and interface.py owner: {child}")
                if interface.exists():
                    safe(interface)
                    owners.append(PackageFunctionOwner(
                        child.name, child, relative, "extension", interface_path=interface,
                    ))
                elif source.exists() or legacy.exists():
                    if source.exists():
                        safe(source)
                    if legacy.exists():
                        safe(legacy)
                    owners.append(PackageFunctionOwner(
                        child.name, child, relative, "package" if source.exists() and legacy.exists() else "script" if source.exists() else "native",
                        source_path=source if source.exists() else None,
                        module_path=legacy if legacy.exists() else None,
                    ))
                else:
                    walk(child, relative)

    walk(root, ())
    return tuple(sorted(owners, key=lambda o: (o.relative_parts, o.kind)))
