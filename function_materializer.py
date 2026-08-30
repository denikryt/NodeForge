"""Physical materialization of editable reusable functions as Blender node groups.

The materializer consumes already-resolved compiler identities and materialization
policy.  Source/catalog resolution and caller-node realization stay in their
feature modules, while the existing compiler callback remains the physical
create/update backend until group transactions are extracted separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, MutableMapping

from .errors import CompileError
from .compiler_identities import FunctionId
from .semantic_ir import IRFunctionMaterialization
from .function_instances import (
    function_group_owner_scope,
    instance_key_for_materialization,
    normalized_source,
    stamp_function_metadata,
    stored_fingerprint,
)


def _frozen_mapping(value: Mapping | None) -> Mapping:
    """Return an immutable shallow copy suitable for a materialization request."""
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True)
class FunctionMaterializationContext:
    """Borrow active compilation state for one nested materialization operation."""

    function_group_cache: MutableMapping
    function_group_transaction: object | None
    function_compilation_trace: object | None


@dataclass(frozen=True)
class MaterializedFunctionGroup:
    """Return one materialized group together with its derived physical identity."""

    group: object
    instance_key: str
    owner_scope: str | None


@dataclass(frozen=True)
class LocalFunctionMaterializationSpec:
    """Resolved physical build request for one script-local reusable function."""

    materialization: IRFunctionMaterialization
    logical_namespace: str
    group_name: str
    source: str
    definition_owner: str
    own_inputs: Mapping[str, object]
    compile_kwargs: Mapping[str, object]
    find_existing: Callable[..., object | None]
    is_live_group: Callable[[object], bool]
    finalize_group: Callable[[object, str, str], None]

    def __post_init__(self):
        """Freeze mutable request mappings at the materializer boundary."""
        object.__setattr__(self, "own_inputs", _frozen_mapping(self.own_inputs))
        object.__setattr__(self, "compile_kwargs", _frozen_mapping(self.compile_kwargs))


@dataclass(frozen=True)
class LibraryFunctionMaterializationSpec:
    """Resolved editable catalog definition ready for physical materialization."""

    namespace: str
    name: str
    record: object
    source: str
    backend_builtins: Mapping[str, object]
    function_id: FunctionId
    materialization: IRFunctionMaterialization | None
    group_name: str
    backend_signature: str
    find_existing: Callable[..., object | None]
    write_package_metadata: Callable[[object, object], None]

    def __post_init__(self):
        """Freeze package-local backend helpers at the request boundary."""
        object.__setattr__(self, "backend_builtins", _frozen_mapping(self.backend_builtins))


@dataclass(frozen=True)
class LibraryFunctionUpdateSpec:
    """Resolved selected-root editable catalog update request."""

    namespace: str
    name: str
    record: object
    source: str
    backend_builtins: Mapping[str, object]
    function_id: FunctionId
    group: object
    group_name: str
    backend_signature: str
    write_package_metadata: Callable[[object, object], None]

    def __post_init__(self):
        """Freeze package-local backend helpers at the request boundary."""
        object.__setattr__(self, "backend_builtins", _frozen_mapping(self.backend_builtins))


class FunctionMaterializer:
    """Materialize canonical reusable-function definitions as Blender node groups."""

    def __init__(self, *, compile_group_callback):
        """Store only the legacy physical group-build callback."""
        self._compile_group_callback = compile_group_callback

    def _compile_or_update(self, source: str, group_name: str, *, existing_group=None, compile_kwargs=None):
        """Invoke the existing create/update backend with an exact prepared kwarg set."""
        kwargs = dict(compile_kwargs or {})
        if existing_group is not None:
            kwargs["existing_group"] = existing_group
        # FUNCTION_MATERIALIZER_TRANSACTION_MIGRATION: Reusable-function artifact ownership
        # now lives in FunctionMaterializer, but physical create/update/cutover still enters
        # the legacy compiler group-build callback and FunctionGroupBuildTransaction. Keep
        # that callback contract exact in this behavior-preserving stage. Remove this bridge
        # when Blender group build/update transactions are extracted behind an explicit
        # materialization backend service and FunctionMaterializer no longer calls compiler.py.
        return self._compile_group_callback(source, group_name, **kwargs)

    def materialize_local(
        self,
        spec: LocalFunctionMaterializationSpec,
        context: FunctionMaterializationContext,
    ) -> MaterializedFunctionGroup:
        """Create or reuse one script-local helper under the existing local contract."""
        if context is None:
            raise CompileError("Internal error: local reusable materialization requires compilation context")

        materialization = spec.materialization
        instance_key = instance_key_for_materialization(materialization)
        callee = materialization.callee
        owner_scope = function_group_owner_scope(
            "LOCAL_DEF",
            callee.definition_owner,
            callee.name,
            callee.signature,
            instance_key=instance_key,
        )
        cache_key = ("local-def", callee, instance_key or "SHARED")
        function_cache = context.function_group_cache
        function_group = function_cache.get(cache_key)
        if function_group is not None and spec.is_live_group(function_group):
            self._record_child(context.function_compilation_trace, owner_scope, function_group)
            return MaterializedFunctionGroup(function_group, instance_key, owner_scope)

        existing = spec.find_existing(
            namespace=spec.logical_namespace,
            function_name=callee.name,
            signature=callee.signature,
            definition_owner=spec.definition_owner,
            instance_key=instance_key,
            transaction=context.function_group_transaction,
        )
        compile_kwargs = dict(spec.compile_kwargs)
        compile_kwargs.update({
            "function_group_cache": function_cache,
            "function_group_transaction": context.function_group_transaction,
            "function_group_owner_scope": owner_scope,
            "function_definition_owner": spec.definition_owner,
            "function_compilation_trace": context.function_compilation_trace,
            "function_compilation_inputs": dict(spec.own_inputs),
            "function_definition_identity": callee.stable_key(),
            "function_instance_key": instance_key,
        })
        if existing is not None:
            compile_kwargs["preserve_if_equivalent"] = True
        function_group = self._compile_or_update(
            spec.source,
            spec.group_name,
            existing_group=existing,
            compile_kwargs=compile_kwargs,
        )
        fingerprint = stored_fingerprint(function_group)
        spec.finalize_group(function_group, fingerprint, instance_key)
        function_cache[cache_key] = function_group
        self._record_child(context.function_compilation_trace, owner_scope, function_group)
        return MaterializedFunctionGroup(function_group, instance_key, owner_scope)

    def materialize_library(
        self,
        spec: LibraryFunctionMaterializationSpec,
        context: FunctionMaterializationContext | None = None,
    ) -> MaterializedFunctionGroup:
        """Create or reuse one editable catalog group while preserving path semantics."""
        namespace = spec.namespace
        materialization = spec.materialization

        if namespace == "local":
            if materialization is not None:
                raise CompileError("Internal error: Local catalog materialization cannot use reusable-call IR yet")
            group = self._materialize_local_catalog(spec, context)
            return MaterializedFunctionGroup(group, "", None)

        if materialization is not None:
            if context is None:
                raise CompileError("Internal error: reusable library materialization requires compilation context")
            if materialization.callee != spec.function_id:
                raise CompileError("Internal error: reusable-call materialization identity mismatch")
            instance_key = instance_key_for_materialization(materialization)
            use_reusable_cache = True
        else:
            instance_key = ""
            use_reusable_cache = False

        function_id = spec.function_id
        owner_scope = function_group_owner_scope(
            "LIBRARY",
            namespace,
            function_id.package_id,
            function_id.name,
            instance_key=instance_key or None,
        )
        cache_key = ("library", function_id, instance_key or "SHARED")
        if use_reusable_cache and cache_key in context.function_group_cache:
            return MaterializedFunctionGroup(context.function_group_cache[cache_key], instance_key, owner_scope)

        transaction = context.function_group_transaction if context is not None else None
        trace = context.function_compilation_trace if context is not None else None
        cache = context.function_group_cache if use_reusable_cache else None
        definition_identity = function_id.stable_key()
        own_inputs = {
            "kind": "library",
            "namespace": namespace,
            "package_id": function_id.package_id,
            "package_version": getattr(spec.record, "package_version", "") or "",
            "name": spec.name,
            "source": normalized_source(spec.source),
            "backend_signature": spec.backend_signature,
        }
        existing = spec.find_existing(spec.record, instance_key=instance_key, transaction=transaction)
        compile_kwargs = {
            "backend_builtins": dict(spec.backend_builtins),
            "function_group_cache": cache,
            "function_group_transaction": transaction,
            "function_group_owner_scope": owner_scope,
            "function_definition_owner": definition_identity,
            "function_compilation_trace": trace,
            "function_compilation_inputs": own_inputs,
            "function_definition_identity": definition_identity,
            "function_instance_key": instance_key,
        }
        if existing is not None:
            compile_kwargs["preserve_if_equivalent"] = True
        group = self._compile_or_update(
            spec.source,
            spec.group_name,
            existing_group=existing,
            compile_kwargs=compile_kwargs,
        )
        if use_reusable_cache:
            context.function_group_cache[cache_key] = group
        if materialization is not None:
            self._record_child(trace, owner_scope, group)
        self._finalize_catalog_metadata(spec, group, instance_key)
        return MaterializedFunctionGroup(group, instance_key, owner_scope)

    def _materialize_local_catalog(
        self,
        spec: LibraryFunctionMaterializationSpec,
        context: FunctionMaterializationContext | None,
    ):
        """Build a fresh Local catalog dependency without generic reusable caching."""
        transaction = context.function_group_transaction if context is not None else None
        trace = context.function_compilation_trace if context is not None else None
        local_owner = function_group_owner_scope("LIBRARY", "local", "", spec.name)
        compile_kwargs = {
            "backend_builtins": dict(spec.backend_builtins),
            "function_group_transaction": transaction,
            "function_group_owner_scope": local_owner,
            "function_definition_owner": local_owner,
            "function_compilation_trace": trace,
        }
        group = self._compile_or_update(spec.source, spec.group_name, compile_kwargs=compile_kwargs)
        frame = getattr(trace, "current", None)
        if frame is not None:
            frame.mark_unproven("local catalog dependency")
        self._finalize_catalog_metadata(spec, group, "")
        return group

    def _finalize_catalog_metadata(self, spec: LibraryFunctionMaterializationSpec, group, instance_key: str) -> None:
        """Apply the legacy best-effort editable-catalog metadata contract."""
        # FUNCTION_MATERIALIZER_CATALOG_METADATA_MIGRATION: Editable catalog groups keep
        # the legacy post-build metadata contract in this behavior-preserving stage. Package
        # functions/examples publish cache/trace before package/function/source metadata;
        # Local catalog groups mark freshness unproven before the same metadata block; all
        # metadata write failures remain best-effort/suppressed. Do not promote these errors
        # to materialization failure because the legacy compiler callback may already have
        # committed FunctionGroupBuildTransaction before returning. Remove this boundary
        # when build/update, required metadata, publication, and commit share one explicit
        # rollback-capable transaction backend.
        try:
            spec.write_package_metadata(group, spec.record)
            stamp_function_metadata(
                group,
                instance_key=instance_key,
                definition_owner=spec.function_id.stable_key(),
                fingerprint=stored_fingerprint(group),
            )
            group["nodeforge_library_source"] = spec.source
        except Exception:
            pass

    def update_library_group(self, spec: LibraryFunctionUpdateSpec):
        """Update exactly one provenance-validated editable catalog root group."""
        stable_function_id = spec.function_id.stable_key()
        owner_scope = function_group_owner_scope(
            "LIBRARY",
            spec.namespace,
            spec.function_id.package_id,
            spec.function_id.name,
        )
        compile_kwargs = {
            "backend_builtins": dict(spec.backend_builtins),
            "function_group_owner_scope": owner_scope,
            "function_definition_owner": stable_function_id,
            "function_compilation_inputs": {
                "kind": "library-root",
                "namespace": spec.namespace,
                "package_id": spec.function_id.package_id,
                "package_version": getattr(spec.record, "package_version", "") or "",
                "name": spec.name,
                "source": normalized_source(spec.source),
                "backend_signature": spec.backend_signature,
            },
            "function_definition_identity": stable_function_id,
            "preserve_if_equivalent": True,
        }
        updated = self._compile_or_update(
            spec.source,
            spec.group_name,
            existing_group=spec.group,
            compile_kwargs=compile_kwargs,
        )
        # FUNCTION_MATERIALIZER_RELOAD_METADATA_MIGRATION: Explicit editable-root reload keeps
        # its legacy failure contract in this behavior-preserving stage: physical update may
        # already be committed when required package/source metadata is written, and metadata
        # errors still propagate instead of being suppressed. Do not merge this branch with
        # ordinary imported best-effort metadata handling. Remove this boundary when selected-
        # root update and required reload metadata execute inside one explicit rollback-capable
        # Blender group transaction before commit.
        spec.write_package_metadata(updated, spec.record)
        updated["nodeforge_library_source"] = spec.source
        return updated

    @staticmethod
    def _record_child(trace, owner_scope: str, group) -> None:
        """Record one actually materialized child using the existing trace semantics."""
        frame = getattr(trace, "current", None)
        if frame is not None:
            frame.record_child(owner_scope, stored_fingerprint(group))


__all__ = [
    "FunctionMaterializationContext",
    "FunctionMaterializer",
    "LibraryFunctionMaterializationSpec",
    "LibraryFunctionUpdateSpec",
    "LocalFunctionMaterializationSpec",
    "MaterializedFunctionGroup",
]
