"""Pure whole-group semantic preparation for NodeForge source-backed compilation."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
from . import builtin_registry
from .call_resolution import CallableEnvironment
from .callable_contracts import (
    GroupInputContract,
    GroupInterfaceContract,
    GroupOutputContract,
    canonicalize_group_input_default,
    project_group_input_layout,
)
from ..compiler_identities import BindingId, GroupCompilationIdentity
from .constants import TYPE_FLOAT, TYPE_INT, TYPE_TOKEN_NAMES, _ALLOWED_CONSTS
from .residualization import _infer_input_types, _preprocess_compile_time
from ..errors import CompileError
from ..nf_types import NFType
from ..extensions.contracts import ExtensionCallableId
from .source_bindings import LibraryBinding, PackageNamespaceBinding
from ..extensions.registry import library_owner_key
from .parsing import _assigned_names, _binding_names, _collect_inputs, _extract_function_imports, _needs_geometry_io, _parse_source, normalized_statements
from .runtime_bindings import RuntimeBindingSymbol, allows_existing_top_level_shadow, format_reserved_binding_label
from .body import BasicBodyCompilation, lower_basic_body
from .ir import IRBody, IRIf, IRInputDeclaration, IRPanelDeclaration, IRRepeat


@dataclass(frozen=True)
class SemanticGroupCompilation:
    """Capture one complete Blender-independent group compilation artifact."""

    source: str
    identity: GroupCompilationIdentity
    normalized_lowered_source: str
    body: IRBody
    interface: GroupInterfaceContract
    geometry_mode: bool
    extension_dependencies: tuple[tuple[tuple[str, ...], str], ...] = ()

    def __post_init__(self) -> None:
        """Freeze owner-independent metadata and reject backend objects by construction."""
        if not isinstance(self.source, str):
            raise TypeError("SemanticGroupCompilation.source must be source text")
        if not isinstance(self.identity, GroupCompilationIdentity):
            raise TypeError("SemanticGroupCompilation.identity must be GroupCompilationIdentity")
        if not isinstance(self.normalized_lowered_source, str):
            raise TypeError("normalized_lowered_source must be a string")
        if not isinstance(self.body, IRBody):
            raise TypeError("SemanticGroupCompilation.body must be IRBody")
        if not isinstance(self.interface, GroupInterfaceContract):
            raise TypeError("SemanticGroupCompilation.interface must be GroupInterfaceContract")
        if not isinstance(self.geometry_mode, bool):
            raise TypeError("geometry_mode must be bool")
        rows = tuple((tuple(owner), fingerprint) for owner, fingerprint in self.extension_dependencies)
        if any(not owner or not all(isinstance(part, str) and part for part in owner) for owner, _ in rows):
            raise ValueError("extension dependency owners must be canonical non-empty string tuples")
        if any(not isinstance(fingerprint, str) or not fingerprint for _, fingerprint in rows):
            raise ValueError("extension dependency fingerprints must be non-empty strings")
        if len({owner for owner, _ in rows}) != len(rows):
            raise ValueError("extension dependency owner keys must be unique")
        if rows != tuple(sorted(rows, key=lambda row: row[0])):
            raise ValueError("extension dependency rows must be sorted by owner key")
        object.__setattr__(self, "extension_dependencies", rows)


def _validate_import_bindings(
    import_pairs,
    body_stmts,
    local_function_defs,
    resolved_environment,
    inherited_imports=None,
    additional_reserved_names=frozenset(),
):
    """Validate and return source-local namespace-aware catalog bindings."""
    imported: dict[str, LibraryBinding] = {}
    local_bindings = _binding_names(body_stmts)
    reserved_names = (
        set(builtin_registry.BUILTIN_NAMES)
        | {"output", "store", "panel"}
        | set(_ALLOWED_CONSTS)
        | set(TYPE_TOKEN_NAMES)
        | set(additional_reserved_names)
    )

    def validate_pair(namespace, canonical_name, exposed_name, *, inherited=False, inherited_record=None):
        """Validate one imported catalog binding against the frozen environment."""
        catalog = resolved_environment.catalog(namespace)
        record = catalog.find(canonical_name)
        if record is None:
            raise CompileError(f"Unknown {namespace} import: {canonical_name}")
        if inherited_record is not None and inherited_record is not record:
            raise CompileError("Internal error: inherited library binding does not match resolved environment")
        extension_callable_id = None
        if getattr(record, "interface_path", None) is not None and getattr(record, "source_path", None) is None:
            extension_callable_id = ExtensionCallableId(library_owner_key(record), canonical_name)
            if not resolved_environment.extension_registry.contains(extension_callable_id):
                raise CompileError("Internal error: selected native library extension is missing from registry")
        binding = LibraryBinding(namespace, canonical_name, record, extension_callable_id)
        if exposed_name in imported:
            if inherited and imported[exposed_name] == binding:
                return
            raise CompileError(f"Duplicate function import name: {exposed_name}")
        if exposed_name in local_bindings or exposed_name in local_function_defs:
            raise CompileError(f"Function import name conflicts with local binding: {exposed_name}")
        if exposed_name in reserved_names:
            raise CompileError(f"Function import name conflicts with reserved name: {exposed_name}")
        imported[exposed_name] = binding

    for import_request in import_pairs:
        namespace = import_request.module
        if import_request.is_star:
            for library_name in sorted(resolved_environment.catalog(namespace).names(), key=str.lower):
                validate_pair(namespace, library_name, library_name)
        else:
            validate_pair(namespace, import_request.canonical_name, import_request.exposed_name)
    for exposed_name, inherited_binding in dict(inherited_imports or {}).items():
        if isinstance(inherited_binding, LibraryBinding):
            validate_pair(
                inherited_binding.namespace,
                inherited_binding.canonical_name,
                exposed_name,
                inherited=True,
                inherited_record=inherited_binding.record,
            )
        else:
            raise CompileError("Internal error: inherited library binding is not owner-resolved")
    return imported


def _validate_package_import_bindings(
    requests,
    body_stmts,
    local_function_defs,
    resolved_environment,
    inherited_package_bindings=None,
):
    """Resolve exact package namespace imports for one source scope."""
    bindings: dict[str, PackageNamespaceBinding] = {}
    package_ids: dict[str, str] = {}
    local_bindings = _assigned_names(body_stmts)
    reserved_names = (
        set(builtin_registry.BUILTIN_NAMES)
        | {"output", "store", "panel"}
        | set(_ALLOWED_CONSTS)
        | set(TYPE_TOKEN_NAMES)
    )

    def add_binding(import_name, exposed_name, *, inherited=False, inherited_binding=None):
        namespace = resolved_environment.package_by_import_name(import_name)
        if namespace is None:
            raise CompileError(f"Unknown package import: {import_name}")
        binding = PackageNamespaceBinding(exposed_name, namespace)
        if inherited_binding is not None:
            if not isinstance(inherited_binding, PackageNamespaceBinding):
                raise CompileError("Internal error: inherited package binding is not resolved")
            if inherited_binding.namespace is not namespace or inherited_binding.source_name != exposed_name:
                raise CompileError("Internal error: inherited package binding does not match resolved environment")
        existing = bindings.get(exposed_name)
        if existing is not None:
            if inherited and existing == binding:
                return
            raise CompileError(f"Duplicate package import name: {exposed_name}")
        existing_alias = package_ids.get(namespace.package_id)
        if existing_alias is not None and existing_alias != exposed_name:
            raise CompileError(
                f"Package {namespace.import_name!r} is already imported as {existing_alias!r}"
            )
        if exposed_name in local_bindings or exposed_name in local_function_defs:
            raise CompileError(f"Package import name conflicts with local binding: {exposed_name}")
        if exposed_name in reserved_names:
            raise CompileError(f"Package import name conflicts with reserved name: {exposed_name}")
        bindings[exposed_name] = binding
        package_ids[namespace.package_id] = exposed_name

    for request in requests:
        add_binding(request.import_name, request.exposed_name)
    for exposed_name, inherited_binding in dict(inherited_package_bindings or {}).items():
        add_binding(
            inherited_binding.namespace.import_name,
            exposed_name,
            inherited=True,
            inherited_binding=inherited_binding,
        )
    return bindings


def _registered_name_labels(local_function_defs, imported_library_functions, package_namespaces):
    """Return active DSL-owned names and their established reservation labels."""
    labels = {}

    def add(names, label):
        """Add reservation labels without overriding an earlier ownership category."""
        for name in names:
            labels.setdefault(name, label)

    add(builtin_registry.BUILTIN_NAMES, "DSL builtin")
    add({"output", "store", "panel"}, "reserved helper")
    add(_ALLOWED_CONSTS, "compile-time constant")
    add(TYPE_TOKEN_NAMES, "type token")
    add(imported_library_functions, "imported function")
    add(local_function_defs, "local function")
    return labels


def _check_registered_binding(name, labels, *, context="assign", allow_existing_shadow=False):
    """Reject source bindings that collide with non-shadowable registered names."""
    label = labels.get(name)
    if label is None:
        return
    if allow_existing_shadow and allows_existing_top_level_shadow(label):
        return
    if context == "parameter":
        raise CompileError(f"Local function parameter {name} is {format_reserved_binding_label(label)}")
    raise CompileError(f"Cannot assign to {name}: name is {format_reserved_binding_label(label)}")


def _validate_registered_name_bindings(stmts, labels, *, top_level_function_names=None):
    """Validate raw AST binding sites before preprocessing can erase them."""
    top_level_function_names = set(top_level_function_names or ())

    def check_target(target, *, allow_existing_shadow=False):
        """Validate one assignment target against reserved compiler-owned names."""
        if isinstance(target, ast.Name):
            _check_registered_binding(target.id, labels, allow_existing_shadow=allow_existing_shadow)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                check_target(item, allow_existing_shadow=allow_existing_shadow)

    def visit(stmt, *, top_level=False, in_local_function=False):
        """Traverse one statement while preserving top-level/local shadowing rules."""
        allow_existing_shadow = not in_local_function
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                check_target(target, allow_existing_shadow=allow_existing_shadow)
            return
        if isinstance(stmt, ast.AugAssign):
            check_target(stmt.target, allow_existing_shadow=allow_existing_shadow)
            return
        if isinstance(stmt, ast.For):
            check_target(stmt.target, allow_existing_shadow=allow_existing_shadow)
            for sub in stmt.body:
                visit(sub, in_local_function=in_local_function)
            for sub in stmt.orelse:
                visit(sub, in_local_function=in_local_function)
            return
        if isinstance(stmt, ast.If):
            for sub in stmt.body:
                visit(sub, in_local_function=in_local_function)
            for sub in stmt.orelse:
                visit(sub, in_local_function=in_local_function)
            return
        if isinstance(stmt, ast.FunctionDef):
            if not (top_level and stmt.name in top_level_function_names and labels.get(stmt.name) == "local function"):
                _check_registered_binding(stmt.name, labels)
            for arg in list(stmt.args.posonlyargs) + list(stmt.args.args) + list(stmt.args.kwonlyargs):
                if arg.arg == "__unique__":
                    raise CompileError("Local function parameter __unique__ is reserved by the compiler")
                _check_registered_binding(arg.arg, labels, context="parameter")
            if stmt.args.vararg is not None:
                _check_registered_binding(stmt.args.vararg.arg, labels, context="parameter")
            if stmt.args.kwarg is not None:
                _check_registered_binding(stmt.args.kwarg.arg, labels, context="parameter")
            for sub in stmt.body:
                visit(sub, in_local_function=True)

    for stmt in stmts:
        visit(stmt, top_level=True)


def _validate_interface_directive_placement(stmts):
    """Allow ``panel()`` only as a direct expression in the immediate group body."""
    def is_panel_call(node):
        """Return whether *node* is a direct call to the panel declaration builtin."""
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "panel"

    for stmt in stmts:
        direct_call = stmt.value if isinstance(stmt, ast.Expr) and is_panel_call(stmt.value) else None
        for node in ast.walk(stmt):
            if is_panel_call(node) and node is not direct_call:
                raise CompileError("panel() is a top-level interface declaration")


def _walk_body_statements(body: IRBody):
    """Yield body statements recursively in physical semantic traversal order."""
    for statement in body.statements:
        yield statement
        if isinstance(statement, IRIf):
            yield from _walk_body_statements(statement.true_body)
            yield from _walk_body_statements(statement.false_body)
        elif isinstance(statement, IRRepeat):
            yield from _walk_body_statements(statement.body)


def _build_interface_contract(
    *,
    geometry_mode: bool,
    implicit_inputs,
    body_compilation: BasicBodyCompilation,
) -> GroupInterfaceContract:
    """Infer final callable input/output order from semantic body facts only."""
    base_inputs: list[GroupInputContract] = []
    if geometry_mode:
        base_inputs.append(
            GroupInputContract(
                source_name=None,
                display_name="Geometry",
                typ=NFType.GEOMETRY,
                default=None,
                has_default=False,
                interface_origin=None,
            )
        )
    for source_name, binding_id, typ, default in implicit_inputs:
        base_inputs.append(
            GroupInputContract(
                source_name=source_name,
                display_name=source_name,
                typ=typ,
                default=canonicalize_group_input_default(typ, default),
                has_default=True,
                interface_origin=binding_id,
            )
        )
    panels: list[IRPanelDeclaration] = []
    for statement in _walk_body_statements(body_compilation.body):
        if isinstance(statement, IRInputDeclaration):
            has_default = statement.typ in {NFType.FLOAT, NFType.INT, NFType.BOOL, NFType.VECTOR, NFType.STRING}
            default = canonicalize_group_input_default(statement.typ, statement.default) if has_default else None
            base_inputs.append(
                GroupInputContract(
                    source_name=statement.target_name,
                    display_name=statement.display_name,
                    typ=statement.typ,
                    default=default,
                    has_default=has_default,
                    interface_origin=statement.declaration_id,
                )
            )
        elif isinstance(statement, IRPanelDeclaration):
            panels.append(statement)
    final_inputs = project_group_input_layout(tuple(base_inputs), tuple(panels))

    outputs: list[GroupOutputContract] = []
    used_names = {"Geometry"} if geometry_mode else set()
    if geometry_mode:
        outputs.append(GroupOutputContract(0, "Geometry", NFType.GEOMETRY))
    source_outputs = (
        body_compilation.output_summary.explicit_outputs
        if body_compilation.output_summary.explicit_outputs
        else ((body_compilation.output_summary.auto_output,) if body_compilation.output_summary.auto_output is not None else ())
    )
    for requested_name, typ in source_outputs:
        base = requested_name or "out"
        display = base
        suffix = 2
        while display in used_names:
            display = f"{base}_{suffix}"
            suffix += 1
        used_names.add(display)
        outputs.append(GroupOutputContract(len(outputs), display, typ))
    return GroupInterfaceContract(tuple(final_inputs), tuple(outputs))


def analyze_group_source(
    source: str,
    *,
    compilation_identity: GroupCompilationIdentity,
    resolved_environment,
    inherited_local_functions=None,
    inherited_imported_library_functions=None,
    inherited_package_namespaces=None,
    helper_namespace: str = "NodeForge Group",
    source_callable_session=None,
) -> SemanticGroupCompilation:
    """Compile one source snapshot to typed group IR before any Blender mutation."""
    if not isinstance(compilation_identity, GroupCompilationIdentity):
        raise TypeError("compilation_identity must be GroupCompilationIdentity")
    raw_stmts = _parse_source(source)
    raw_body_stmts, import_pairs, package_imports = _extract_function_imports(raw_stmts)

    local_function_defs = dict(inherited_local_functions or {})
    body_stmts = []
    for stmt in raw_body_stmts:
        if isinstance(stmt, ast.FunctionDef):
            if stmt.name in local_function_defs:
                raise CompileError(f"Duplicate local function: {stmt.name}")
            local_function_defs[stmt.name] = stmt
        else:
            body_stmts.append(stmt)

    resolved_environment.catalog("examples").names()
    package_bindings = _validate_package_import_bindings(
        package_imports,
        raw_body_stmts,
        local_function_defs,
        resolved_environment,
        inherited_package_bindings=inherited_package_namespaces,
    )
    imported = _validate_import_bindings(
        import_pairs,
        raw_body_stmts,
        local_function_defs,
        resolved_environment,
        inherited_imports=inherited_imported_library_functions,
        additional_reserved_names=frozenset(package_bindings),
    )
    reserved_name_labels = _registered_name_labels(local_function_defs, imported, package_bindings)
    _validate_registered_name_bindings(
        raw_body_stmts,
        reserved_name_labels,
        top_level_function_names=local_function_defs,
    )
    _validate_interface_directive_placement(raw_body_stmts)

    preprocessed = _preprocess_compile_time(body_stmts)
    stmts = list(preprocessed.statements)
    callable_names = set(imported) | set(local_function_defs) | set(package_bindings)
    input_names = sorted(
        set(_collect_inputs(stmts, extra_builtin_names=callable_names, consts=preprocessed.final_compile_time.values))
        - set(preprocessed.final_compile_time.values.keys())
    )
    input_types = _infer_input_types(stmts)
    geometry_mode = _needs_geometry_io(stmts)

    initial_runtime_bindings: dict[str, RuntimeBindingSymbol] = {}
    initial_origins = {}
    implicit_inputs = []
    for local_id, input_name in enumerate(input_names):
        typ = input_types.get(input_name, TYPE_FLOAT)
        binding_id = BindingId(compilation_identity.owner_scope, local_id)
        initial_runtime_bindings[input_name] = RuntimeBindingSymbol(binding_id, typ)
        initial_origins[binding_id] = binding_id
        default = 1 if typ is TYPE_INT and input_name == "iterations" else (0 if typ is TYPE_INT else 0.0)
        implicit_inputs.append((input_name, binding_id, typ, default))

    callable_environment = CallableEnvironment(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
        local_functions=local_function_defs,
        imported_functions=imported,
        package_namespaces=package_bindings,
    )
    extension_dependencies: dict[tuple[str, ...], str] = {}
    body_compilation = lower_basic_body(
        stmts,
        initial_runtime_bindings=initial_runtime_bindings,
        initial_compile_time=preprocessed.initial_compile_time,
        reserved_name_labels=reserved_name_labels,
        callable_environment=callable_environment,
        owner_scope=compilation_identity.owner_scope,
        declaration_owner=compilation_identity.declaration_owner,
        geometry_mode=geometry_mode,
        initial_interface_input_origins=initial_origins,
        compile_time_effects_before=preprocessed.effects_before,
        trailing_compile_time_effects=preprocessed.trailing_effects,
        source_callable_session=source_callable_session,
        source_definition_owner=compilation_identity.definition_owner,
        helper_namespace=helper_namespace,
        extension_registry=resolved_environment.extension_registry,
        extension_dependency_sink=extension_dependencies,
    )
    interface = _build_interface_contract(
        geometry_mode=geometry_mode,
        implicit_inputs=tuple(implicit_inputs),
        body_compilation=body_compilation,
    )
    if not geometry_mode and not interface.outputs:
        raise CompileError("Script produced no output. Use out = ..., output(...), set_position(...), or store(...)")
    return SemanticGroupCompilation(
        source=source,
        identity=compilation_identity,
        normalized_lowered_source=normalized_statements(stmts),
        body=body_compilation.body,
        interface=interface,
        geometry_mode=geometry_mode,
        extension_dependencies=tuple(sorted(extension_dependencies.items(), key=lambda row: row[0])),
    )


__all__ = [
    "SemanticGroupCompilation",
    "analyze_group_source",
]
