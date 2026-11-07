"""Root-scoped source callable preparation and snapshot cache."""

from __future__ import annotations

from typing import Mapping

from .callable_contracts import SourceCallableContract, SourceCallableParameter, normalize_callable_keyword
from ..compiler_identities import FunctionId, GroupCompilationIdentity, ModuleId
from ..errors import CompileError
from .source_callables import LocalReturnShape, PreparedSourceCallable, SourceCallablePreparationKey

class SourceCallableSession:
    """Own one root-attempt source snapshot and owner-specific preparation cache."""

    def __init__(self, *, resolved_environment) -> None:
        """Share one eager environment or compiler demand view through recursive preparation."""
        self.resolved_environment = resolved_environment
        self.source_snapshots: dict[FunctionId, str] = {}
        self.module_snapshots = {}
        self.module_environments = {}
        self.prepared: dict[SourceCallablePreparationKey, PreparedSourceCallable] = {}
        self._active_stack: list[FunctionId] = []

    def capture_local_module(self, record):
        """Freeze a selected Local .nf revision once per compilation attempt."""
        from .source_modules import parse_source_module
        if record.namespace != "local" or record.source_path is None or record.source_path.suffix != ".nf":
            raise CompileError("Selected Local module must be a .nf source file")
        module_id = ModuleId("LOCAL", record.source_origin)
        snapshot = self.module_snapshots.get(module_id)
        if snapshot is None:
            snapshot = parse_source_module(record.source_path.read_text(encoding="utf-8"), module_id)
            self.module_snapshots[module_id] = snapshot
        return snapshot

    def callable_environment_for_module(self, snapshot):
        """Resolve an imported module's own names once, without executing its body."""
        from .group import resolve_module_callable_environment
        stored = self.module_snapshots.setdefault(snapshot.module_id, snapshot)
        if stored is not snapshot:
            raise CompileError("Conflicting source revisions for one imported module")
        result = self.module_environments.get(snapshot.module_id)
        if result is None:
            result = resolve_module_callable_environment(snapshot, self.resolved_environment, self)
            self.module_environments[snapshot.module_id] = result
        return result

    def _source_snapshot(self, function_id: FunctionId, source_supplier) -> str:
        """Capture one coherent definition source snapshot for this root attempt."""
        source = self.source_snapshots.get(function_id)
        if source is None:
            source = source_supplier()
            if not isinstance(source, str):
                raise CompileError("Internal error: source-backed callable supplier did not return source text")
            self.source_snapshots[function_id] = source
        return source

    def _prepare(
        self,
        *,
        function_id: FunctionId,
        identity: GroupCompilationIdentity,
        source_supplier,
        local_functions,
        imported_library_functions,
        package_namespaces,
        helper_namespace: str,
        local_namespaces=None,
        local_public_names: tuple[str, ...] | None = None,
        local_hidden_names: tuple[str, ...] = (),
        local_output_count: int | None = None,
        source_record=None,
    ) -> PreparedSourceCallable:
        """Prepare/cache one exact owner-specific semantic group and callable contract."""
        key = SourceCallablePreparationKey(function_id, identity.owner_scope)
        cached = self.prepared.get(key)
        if cached is not None:
            return cached
        if function_id in self._active_stack:
            chain = " -> ".join(item.name for item in self._active_stack + [function_id])
            raise CompileError(f"Recursive source function calls are not supported: {chain}")
        source = self._source_snapshot(function_id, source_supplier)
        self._active_stack.append(function_id)
        try:
            from .group import analyze_group_source
            from .runtime_capabilities import RuntimeRole

            group = analyze_group_source(
                source,
                compilation_identity=identity,
                resolved_environment=self.resolved_environment,
                inherited_local_functions=local_functions,
                inherited_imported_library_functions=imported_library_functions,
                inherited_package_namespaces=package_namespaces,
                inherited_local_namespaces=local_namespaces,
                helper_namespace=helper_namespace,
                source_callable_session=self,
                runtime_role=RuntimeRole.NESTED,
            )
        finally:
            popped = self._active_stack.pop()
            if popped != function_id:
                raise CompileError("Internal error: source-call semantic activity stack was corrupted")

        if group.source != source:
            raise CompileError("Internal error: prepared source callable changed its captured source snapshot")
        if local_public_names is None:
            parameters = tuple(
                SourceCallableParameter(
                    input_index=input_index,
                    source_name=item.source_name,
                    display_name=item.display_name,
                    keyword_key=normalize_callable_keyword(item.display_name),
                    typ=item.typ,
                    public=True,
                )
                for input_index, item in enumerate(group.interface.inputs)
            )
        else:
            inputs_by_name = {
                item.source_name: (input_index, item)
                for input_index, item in enumerate(group.interface.inputs)
                if item.source_name is not None
            }
            parameters_list: list[SourceCallableParameter] = []
            for name in local_public_names + local_hidden_names:
                indexed_item = inputs_by_name.get(name)
                if indexed_item is None:
                    raise CompileError(f"Internal error: local helper semantic interface lost input {name!r}")
                input_index, item = indexed_item
                public = name in local_public_names
                parameters_list.append(
                    SourceCallableParameter(
                        input_index=input_index,
                        source_name=name,
                        display_name=item.display_name,
                        keyword_key=name if public else None,
                        typ=item.typ,
                        public=public,
                        has_default=item.has_default,
                        interface_origin=item.interface_origin,
                    )
                )
            parameters = tuple(parameters_list)
        outputs = group.interface.outputs
        if local_output_count is not None:
            if local_output_count <= 0 or local_output_count > len(outputs):
                raise CompileError("Internal error: local helper semantic output count does not match return shape")
            outputs = outputs[-local_output_count:]
        contract = SourceCallableContract(function_id, parameters, outputs)
        if contract.function_id != key.function_id or group.identity.owner_scope != key.owner_scope:
            raise CompileError("Internal error: prepared source callable cache key does not match semantic result")
        prepared = PreparedSourceCallable(contract, group, source_record)
        self.prepared[key] = prepared
        return prepared

    def prepare_local(
        self,
        *,
        function_id: FunctionId,
        identity: GroupCompilationIdentity,
        generated_source: str,
        explicit_parameter_names: tuple[str, ...],
        hidden_capture_names: tuple[str, ...],
        local_functions,
        imported_library_functions,
        package_namespaces,
        helper_namespace: str,
        local_namespaces=None,
        return_shape: LocalReturnShape,
    ) -> PreparedSourceCallable:
        """Prepare one local specialization under its final shared/unique physical owner."""
        return self._prepare(
            function_id=function_id,
            identity=identity,
            source_supplier=lambda: generated_source,
            local_functions=local_functions,
            imported_library_functions=imported_library_functions,
            package_namespaces=package_namespaces,
            local_namespaces=local_namespaces,
            helper_namespace=helper_namespace,
            local_public_names=tuple(explicit_parameter_names),
            local_hidden_names=tuple(hidden_capture_names),
            local_output_count=len(return_shape.elements),
        )

    def prepare_definition(
        self,
        *,
        function_id: FunctionId,
        identity: GroupCompilationIdentity,
        fn,
        callable_environment,
        compile_time_values,
        helper_namespace: str,
        source_record=None,
        source_module=None,
    ) -> PreparedSourceCallable:
        """Compile one FunctionDef AST directly under its physical shared/unique owner."""
        import ast

        from .function_bodies import compile_function_definition

        key = SourceCallablePreparationKey(function_id, identity.owner_scope)
        cached = self.prepared.get(key)
        if cached is not None:
            return cached
        if function_id in self._active_stack:
            chain = " -> ".join(item.name for item in self._active_stack + [function_id])
            raise CompileError(f"Recursive source function calls are not supported: {chain}")
        source = ast.unparse(fn)
        if source_module is not None:
            stored = self.module_snapshots.setdefault(source_module.module_id, source_module)
            if stored is not source_module:
                raise CompileError("Source module revision changed during one compilation attempt")
        existing_source = self.source_snapshots.setdefault(function_id, source)
        if existing_source != source:
            raise CompileError("Source function changed during a single preparation attempt")
        self._active_stack.append(function_id)
        try:
            group = compile_function_definition(
                fn,
                compilation_identity=identity,
                callable_environment=callable_environment,
                resolved_environment=self.resolved_environment,
                compile_time_values=compile_time_values,
                source_callable_session=self,
                helper_namespace=helper_namespace,
            )
        finally:
            popped = self._active_stack.pop()
            if popped != function_id:
                raise CompileError("Internal error: source function compilation stack was corrupted")
        if group.identity.owner_scope != key.owner_scope:
            raise CompileError("Internal error: function body changed its physical owner")
        inputs = {item.source_name: (index, item) for index, item in enumerate(group.interface.inputs)}
        parameters = []
        for argument in fn.args.args:
            indexed = inputs.get(argument.arg)
            if indexed is None:
                raise CompileError(f"Internal error: function interface lost parameter {argument.arg!r}")
            input_index, contract = indexed
            parameters.append(SourceCallableParameter(
                input_index=input_index,
                source_name=argument.arg,
                display_name=contract.display_name,
                keyword_key=argument.arg,
                typ=contract.typ,
                public=True,
                has_default=contract.has_default,
                interface_origin=contract.interface_origin,
            ))
        prepared = PreparedSourceCallable(
            SourceCallableContract(function_id, tuple(parameters), group.interface.outputs),
            group,
            source_record,
        )
        self.prepared[key] = prepared
        return prepared

    def prepare_library(
        self,
        *,
        function_id: FunctionId,
        identity: GroupCompilationIdentity,
        record,
    ) -> PreparedSourceCallable:
        """Prepare one pure source-only catalog entry without importing Python modules."""
        from ..compiler_identities import library_function_id_for_record
        if function_id != library_function_id_for_record(record):
            raise CompileError("Source preparation FunctionId does not match selected canonical record")
        source_path = getattr(record, "source_path", None)
        if source_path is None or getattr(record, "module_path", None) is not None:
            raise CompileError("Internal error: prepare_library requires a pure source-only record")
        return self._prepare(
            function_id=function_id,
            identity=identity,
            source_supplier=lambda: source_path.read_text(encoding="utf-8"),
            local_functions={},
            imported_library_functions={},
            package_namespaces={},
            helper_namespace=getattr(record, "name", function_id.name),
            source_record=record,
        )

    def get_prepared(self, key: SourceCallablePreparationKey) -> PreparedSourceCallable:
        """Return the exact cached owner-specific artifact required by backend lowering."""
        try:
            return self.prepared[key]
        except KeyError as exc:
            raise CompileError("Internal error: source-call backend requested an unprepared callable") from exc



__all__ = ["SourceCallableSession"]
