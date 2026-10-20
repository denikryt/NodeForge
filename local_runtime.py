"""Adapt registered Local Python files to the existing Extension API v2.

Interface and physical adapter modules are generated from statically captured source.
The selected user's implementation is executed only in an explicit implementation
phase under the ordinary source snapshot loader, after verifying its active source owner.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from .errors import CompileError
from .extensions.contracts import ExtensionCallableId
from .extensions.registry import (
    ExtensionOwnerSession, capture_owner_code_snapshot_from_sources,
)
from .local_sources import is_registered_local_python_source
from .mesh_compute import MeshComputeDeclaration, parse_mesh_compute_source


def local_runtime_owner_key(record) -> tuple[str, ...]:
    """Identify a registered Local Python owner by its exact canonical physical file."""
    if record.kind != "runtime" or record.source_path is None:
        raise CompileError("Internal error: Mesh Compute runtime requires a selected Python source record")
    if record.namespace == "local":
        return ("local-runtime", record.source_origin)
    if record.namespace == "examples":
        # Package owner and physical source both participate; package reinstall
        # must not inherit a previous executable callback's owner/session.
        return ("examples-runtime", record.package_id or "core", record.source_origin)
    raise CompileError("Executable Mesh Compute source must belong to Local or Examples")


def _interface_source(declaration: MeshComputeDeclaration) -> str:
    """Render a declaration-only v2 interface without evaluating user Python."""
    typ = {"float": "Float", "int": "Int", "bool": "Bool"}
    params = ["geometry: Annotated[Geometry, EvaluationMode.RUNTIME_ONLY]"]
    for name, kind, default in declaration.parameters:
        suffix = "" if default is inspect.Parameter.empty else f" = {default!r}"
        # v2's RUNTIME_ONLY forbids defaults; COMPILE_TIME_OR_RUNTIME still
        # resolves both a supplied GN socket and a detached literal default.
        mode = "RUNTIME_ONLY" if default is inspect.Parameter.empty else "COMPILE_TIME_OR_RUNTIME"
        params.append(f"{name}: Annotated[{typ[kind]}, EvaluationMode.{mode}]{suffix}")
    return (
        "from typing import Annotated\n"
        f"from {__package__} import EvaluationMode, Geometry, Float, Int, Bool\n"
        "EXTENSION_API = 2\n"
        f"EXTENSIONS = {{{declaration.name!r}: '.backend:{declaration.name}'}}\n"
        f"def {declaration.name}({', '.join(params)}) -> Geometry:\n    ...\n"
    )


def _backend_source(declaration: MeshComputeDeclaration, source_origin: str, namespace: str, package_id: str) -> str:
    """Render a small builder that delegates to the shared Blender mesh-compute method."""
    parameters = [name for name, _, _ in declaration.parameters]
    signature = ', '.join(['context', 'geometry', *parameters])
    attrs = ', '.join(f'{name!r}: {name}' for name in parameters)
    return (
        f"from .kernel import {declaration.name} as _mesh_compute_callback\n"
        f"def {declaration.name}({signature}):\n"
        f"    if getattr(_mesh_compute_callback, '__nodeforge_mesh_compute__', None) != {repr((declaration.reads, declaration.writes))}:\n"
        "        raise ValueError('Mesh Compute declaration changed between static validation and execution')\n"
        f"    return context.mesh_compute(geometry, kernel=_mesh_compute_callback, parameters={{{attrs}}}, "
        f"reads={declaration.reads!r}, writes={declaration.writes!r}, source_origin={source_origin!r}, "
        f"source_namespace={namespace!r}, source_package_id={package_id!r})\n"
    )


def runtime_source_active(namespace: str, source_path: str | Path, package_id: str = "") -> bool:
    """Check current ownership of a recorded runtime source before code execution."""
    source = Path(source_path).resolve()
    if namespace == "local":
        return is_registered_local_python_source(source)
    if namespace != "examples" or source.suffix != ".py" or not source.is_file():
        return False
    if package_id:
        from .packages import active_package_records
        for package in active_package_records():
            if package.manifest.package_id == package_id:
                root = package.manifest.root_for("examples")
                return bool(root is not None and source.is_relative_to(root))
        return False
    from .catalog import catalog_dir
    root = Path(catalog_dir("examples")).resolve()
    return source != root and source.is_relative_to(root)


def capture_local_runtime_session(record) -> tuple[ExtensionCallableId, ExtensionOwnerSession]:
    """Capture one registered Local source and its v2 adapter without running its code."""
    owner_key = local_runtime_owner_key(record)
    from .catalog import _path_key
    if record.source_origin != _path_key(record.source_path):
        raise CompileError("Selected Local runtime source has inconsistent canonical identity")
    if not runtime_source_active(record.namespace, record.source_path, record.package_id):
        raise CompileError(f"Mesh Compute Python source is not an active {record.namespace} owner: {record.source_path}")
    try:
        if Path(record.source_path).stat().st_size > 1_048_576:
            raise CompileError("Local Python source exceeds 1 MiB")
        source = Path(record.source_path).read_bytes()
        decoded = source.decode('utf-8')
    except (OSError, UnicodeError) as exc:
        raise CompileError(f"Could not read Mesh Compute source {record.source_path}: {exc}") from exc
    decl = parse_mesh_compute_source(decoded, stem=record.name, filename=str(record.source_path))
    snapshot = capture_owner_code_snapshot_from_sources(owner_key, {
        'interface.py': _interface_source(decl).encode('utf-8'),
        'backend.py': _backend_source(decl, str(record.source_path), record.namespace, record.package_id).encode('utf-8'),
        'kernel.py': source,
    })
    session = ExtensionOwnerSession(snapshot)
    families, _refs = session.normalize_interface()
    callable_id = ExtensionCallableId(owner_key, record.name)
    if tuple(families) != (callable_id,):
        raise CompileError(f"Invalid Local Mesh Compute interface for {record.name!r}")
    return callable_id, session


__all__ = ["capture_local_runtime_session", "local_runtime_owner_key"]
