"""Physical materialization of editable reusable functions as Blender node groups.

The materializer consumes already-resolved compiler identities and materialization
policy. Source/catalog resolution and caller-node realization stay in their
feature modules, while physical GeometryNodeTree publication is delegated to an
explicit Blender group backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, MutableMapping

from .errors import CompileError
from .group_build_request import BlenderGroupBuildRequest
from .compiler_identities import FunctionId
from .semantic_ir import IRFunctionMaterialization
from .semantic_group import SemanticGroupCompilation
from .function_instances import (
    direct_library_owner_scope,
    function_group_owner_scope,
    function_materialization_owner_scope,
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
    source_callable_session: object | None = None


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
    prepared_compilation: SemanticGroupCompilation
    definition_owner: str
    own_inputs: Mapping[str, object]
    helper_namespace: str | None
    find_existing: Callable[..., object | None]
    is_live_group: Callable[[object], bool]
    finalize_group: Callable[[object, str, str], None]

    def __post_init__(self):
        """Freeze mappings and require one prepared source authority."""
        if not isinstance(self.prepared_compilation, SemanticGroupCompilation):
            raise TypeError("prepared_compilation must be SemanticGroupCompilation")
        object.__setattr__(self, "own_inputs", _frozen_mapping(self.own_inputs))


@dataclass(frozen=True)
class LibraryFunctionMaterializationSpec:
    """Resolved editable catalog definition ready for physical materialization."""

    namespace: str
    name: str
    record: object
    prepared_compilation: SemanticGroupCompilation
    function_id: FunctionId
    source_callable_session: object | None
    materialization: IRFunctionMaterialization | None
    group_name: str
    backend_signature: str
    find_existing: Callable[..., object | None]
    write_package_metadata: Callable[[object, object], None]

    def __post_init__(self):
        """Require one prepared source authority."""
        if not isinstance(self.prepared_compilation, SemanticGroupCompilation):
            raise TypeError("prepared_compilation must be SemanticGroupCompilation")


@dataclass(frozen=True)
class LibraryFunctionUpdateSpec:
    """Resolved selected-root editable catalog update request."""

    namespace: str
    name: str
    record: object
    prepared_compilation: SemanticGroupCompilation
    function_id: FunctionId
    source_callable_session: object | None
    group: object
    group_name: str
    backend_signature: str
    write_package_metadata: Callable[[object, object], None]

    def __post_init__(self):
        """Require one prepared source authority."""
        if not isinstance(self.prepared_compilation, SemanticGroupCompilation):
            raise TypeError("prepared_compilation must be SemanticGroupCompilation")


class FunctionMaterializer:
    """Materialize canonical reusable-function definitions as Blender node groups."""

    def __init__(self, *, group_backend):
        """Store the explicit physical GeometryNodeTree backend."""
        self._group_backend = group_backend


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
        owner_scope = function_materialization_owner_scope(materialization)
        if spec.prepared_compilation.identity.owner_scope != owner_scope:
            raise CompileError("Internal error: prepared local callable owner does not match materialization")
        cache_key = ("local-def", callee, instance_key or "SHARED")
        function_cache = context.function_group_cache
        function_group = function_cache.get(cache_key)
        if function_group is not None and spec.is_live_group(function_group):
            self._record_dependency(
                context.function_compilation_trace, materialization, function_group
            )
            return MaterializedFunctionGroup(function_group, instance_key, owner_scope)

        existing = spec.find_existing(
            namespace=spec.logical_namespace,
            function_name=callee.name,
            signature=callee.signature,
            definition_owner=spec.definition_owner,
            instance_key=instance_key,
            transaction=context.function_group_transaction,
        )
        request = BlenderGroupBuildRequest(
            prepared_compilation=spec.prepared_compilation,
            name=spec.group_name,
            existing_group=existing,
            helper_namespace=spec.helper_namespace,
            function_group_cache=function_cache,
            function_group_transaction=context.function_group_transaction,
            function_compilation_trace=context.function_compilation_trace,
            function_compilation_inputs=dict(spec.own_inputs),
            function_instance_key=instance_key,
            source_callable_session=context.source_callable_session,
            preserve_if_equivalent=existing is not None,
        )
        function_group = self._group_backend.create_or_update(
            request,
            finalize_before_commit=lambda group: spec.finalize_group(
                group, stored_fingerprint(group), instance_key
            ),
        )
        function_cache[cache_key] = function_group
        self._record_dependency(
            context.function_compilation_trace, materialization, function_group
        )
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
        if materialization is not None:
            owner_scope = function_materialization_owner_scope(materialization)
        else:
            owner_scope = direct_library_owner_scope(
                namespace,
                function_id.package_id,
                function_id.name,
            )
        if spec.prepared_compilation.identity.owner_scope != owner_scope:
            raise CompileError("Internal error: prepared library callable owner does not match materialization")
        cache_key = ("library", function_id, instance_key or "SHARED")
        if use_reusable_cache and cache_key in context.function_group_cache:
            group = context.function_group_cache[cache_key]
            self._record_dependency(
                context.function_compilation_trace, materialization, group
            )
            return MaterializedFunctionGroup(group, instance_key, owner_scope)

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
            "source": normalized_source(spec.prepared_compilation.source),
            "backend_signature": spec.backend_signature,
        }
        existing = spec.find_existing(spec.record, instance_key=instance_key, transaction=transaction)
        def finalize_before_commit(group):
            spec.write_package_metadata(group, spec.record)
            stamp_function_metadata(
                group,
                instance_key=instance_key,
                definition_owner=spec.function_id.stable_key(),
                fingerprint=stored_fingerprint(group),
            )
            group["nodeforge_library_source"] = spec.prepared_compilation.source

        request = BlenderGroupBuildRequest(
            prepared_compilation=spec.prepared_compilation,
            name=spec.group_name,
            existing_group=existing,
            function_group_cache=cache,
            function_group_transaction=transaction,
            function_compilation_trace=trace,
            function_compilation_inputs=own_inputs,
            function_instance_key=instance_key,
            source_callable_session=(
                context.source_callable_session if context is not None else spec.source_callable_session
            ),
            preserve_if_equivalent=existing is not None,
        )
        group = self._group_backend.create_or_update(
            request,
            finalize_before_commit=finalize_before_commit,
        )
        if use_reusable_cache:
            context.function_group_cache[cache_key] = group
        if materialization is not None:
            self._record_dependency(trace, materialization, group)
        return MaterializedFunctionGroup(group, instance_key, owner_scope)

    def _materialize_local_catalog(
        self,
        spec: LibraryFunctionMaterializationSpec,
        context: FunctionMaterializationContext | None,
    ):
        """Materialize one build-local Local catalog child with real freshness metadata."""
        local_owner = direct_library_owner_scope("local", spec.function_id.package_id, spec.name)
        if spec.prepared_compilation.identity.owner_scope != local_owner:
            raise CompileError("Internal error: prepared Local catalog owner does not match physical policy")
        transaction = context.function_group_transaction if context is not None else None
        trace = context.function_compilation_trace if context is not None else None
        cache = context.function_group_cache if context is not None else None
        cache_key = ("local-catalog", spec.function_id)
        if cache is not None:
            cached = cache.get(cache_key)
            if cached is not None:
                fingerprint = stored_fingerprint(cached)
                if not fingerprint:
                    raise CompileError("Internal error: cached Local catalog group has no compilation fingerprint")
                self._record_dependency_identity(trace, local_owner, fingerprint)
                return cached

        own_inputs = {
            "kind": "library",
            "namespace": "local",
            "package_id": spec.function_id.package_id,
            "package_version": getattr(spec.record, "package_version", "") or "",
            "name": spec.name,
            "source": normalized_source(spec.prepared_compilation.source),
            "backend_signature": spec.backend_signature,
        }
        def finalize_before_commit(group):
            spec.write_package_metadata(group, spec.record)
            stamp_function_metadata(
                group,
                instance_key="",
                definition_owner=spec.function_id.stable_key(),
                fingerprint=stored_fingerprint(group),
            )
            group["nodeforge_library_source"] = spec.prepared_compilation.source

        request = BlenderGroupBuildRequest(
            prepared_compilation=spec.prepared_compilation,
            name=spec.group_name,
            function_group_transaction=transaction,
            function_compilation_trace=trace,
            function_compilation_inputs=own_inputs,
            source_callable_session=(
                context.source_callable_session if context is not None else spec.source_callable_session
            ),
        )
        group = self._group_backend.create_or_update(
            request,
            finalize_before_commit=finalize_before_commit,
        )
        fingerprint = stored_fingerprint(group)
        if not fingerprint:
            raise CompileError("Internal error: Local catalog materialization produced no compilation fingerprint")
        if cache is not None:
            cache[cache_key] = group
        self._record_dependency_identity(trace, local_owner, fingerprint)
        return group

    def update_library_group(self, spec: LibraryFunctionUpdateSpec):
        """Update one provenance-validated catalog root from prepared semantics."""
        stable_function_id = spec.function_id.stable_key()
        expected_owner_scope = direct_library_owner_scope(
            spec.namespace,
            spec.function_id.package_id,
            spec.function_id.name,
        )
        expected_definition_owner = (
            expected_owner_scope if spec.namespace == "local" else stable_function_id
        )
        identity = spec.prepared_compilation.identity
        if identity.owner_scope != expected_owner_scope:
            raise CompileError("Internal error: prepared library update owner does not match selected root")
        if identity.definition_owner != expected_definition_owner:
            raise CompileError("Internal error: prepared library update definition owner does not match selected root")
        if identity.declaration_owner != stable_function_id:
            raise CompileError("Internal error: prepared library update declaration owner does not match selected root")
        request = BlenderGroupBuildRequest(
            prepared_compilation=spec.prepared_compilation,
            name=spec.group_name,
            existing_group=spec.group,
            function_compilation_inputs={
                "kind": "library-root",
                "namespace": spec.namespace,
                "package_id": spec.function_id.package_id,
                "package_version": getattr(spec.record, "package_version", "") or "",
                "name": spec.name,
                "source": normalized_source(spec.prepared_compilation.source),
                "backend_signature": spec.backend_signature,
            },
            source_callable_session=spec.source_callable_session,
            preserve_if_equivalent=True,
        )

        def finalize_before_commit(group):
            spec.write_package_metadata(group, spec.record)
            group["nodeforge_library_source"] = spec.prepared_compilation.source

        return self._group_backend.create_or_update(
            request,
            finalize_before_commit=finalize_before_commit,
        )

    @staticmethod
    def _record_dependency_identity(trace, owner_scope: str, fingerprint: str | None) -> None:
        """Record one realized dependency using canonical owner/fingerprint facts."""
        frame = getattr(trace, "current", None)
        if frame is not None:
            frame.record_dependency_identity(owner_scope, fingerprint)

    @staticmethod
    def _record_dependency(trace, materialization: IRFunctionMaterialization, group) -> None:
        """Record one successful reusable access with its realized fingerprint."""
        frame = getattr(trace, "current", None)
        if frame is not None:
            frame.record_dependency(materialization, stored_fingerprint(group))


__all__ = [
    "FunctionMaterializationContext",
    "FunctionMaterializer",
    "LibraryFunctionMaterializationSpec",
    "LibraryFunctionUpdateSpec",
    "LocalFunctionMaterializationSpec",
    "MaterializedFunctionGroup",
]
