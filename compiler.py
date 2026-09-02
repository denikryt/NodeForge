"""Public compiler facade and high-level Geometry Nodes group assembly."""

import ast
import functools
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import bpy

from .constants import TYPE_FLOAT, TYPE_INT, TYPE_TOKEN_NAMES, _ALLOWED_CONSTS
from .errors import CompileError
from .compiler_identities import BindingId, CallSiteId, FunctionId
from .semantic_ir import IRFunctionMaterialization, IRFunctionMaterializationMode
from .values import Value, make_value
from .nodes import (
    _new_node,
    _value,
    _string_value,
    _compare,
    _combine_xyz_mixed,
    _socket_type_for,
)
from .parsing import _parse_source, _extract_function_imports, _binding_names, _collect_inputs, _needs_geometry_io
from .consteval import _const_eval, _preprocess_compile_time, _infer_input_types, _is_const_vector
from .storage import (
    _reset_node_group,
    _store_group_source,
    _extract_group_source,
    _get_or_create_scratch_text,
    _replace_text_contents,
    INPUT_DEFAULTS_PROP,
)
from .interface import _set_socket_default, _set_interface_socket_default, _record_group_input_default
from .update import (
    _apply_group_defaults_to_node,
    _capture_node_external_state,
    _restore_node_external_state,
    _capture_group_external_state,
    _restore_group_external_state,
)
from .library import (
    LibraryEntryRecord,
    materialize_library_entry_group_for_record,
    update_materialized_library_entry_group_for_record,
)
from .statements import _unique_output_name
from .compile_time import reject_compile_time_object
from . import expression_compiler
from .builtins import registry as builtin_registry
from .function_instances import (
    FUNCTION_ROOT_OWNER_ID_PROP,
    FunctionCallModifiers,
    FunctionCompilationTrace,
    function_group_owner_scope as make_function_group_owner_scope,
    interface_contract,
    new_root_owner_id,
    normalized_statements,
    stamp_function_metadata,
    stored_fingerprint,
    stored_interface_contract,
    validate_root_owner_id,
)

from .statement_compiler import GroupBuildContext, compile_statements
from .semantic_analysis import RuntimeBindingSymbol
from .blender_group_backend import BlenderGroupBackend, BlenderGroupBuildTransaction
from .resolved_environment import ResolvedEnvironment, resolve_environment

# BLENDER_TRANSACTION_LEGACY_ALIAS_MIGRATION: Keep the historical transaction class
# names importable from compiler.py for one compatibility stage after physical Blender
# transaction ownership moves to blender_group_backend.py. New production code must
# import BlenderGroupBuildTransaction from the Blender group backend module and must not
# depend on these aliases. Remove them after the next compatibility sweep confirms no
# supported external/test consumer imports FunctionGroupBuildTransaction or
# LocalHelperBuildTransaction from compiler.py.
FunctionGroupBuildTransaction = BlenderGroupBuildTransaction
LocalHelperBuildTransaction = BlenderGroupBuildTransaction



@dataclass(frozen=True)
class LibraryBinding:
    """Resolved source-local binding to one catalog entry."""

    namespace: str
    canonical_name: str
    record: LibraryEntryRecord

    def __post_init__(self) -> None:
        """Require the binding label to match its exact resolved catalog record."""
        if self.record.namespace != self.namespace or self.record.name != self.canonical_name:
            raise ValueError("Library binding does not match its resolved record")


@dataclass(frozen=True)
class RuntimeBindingSnapshot:
    """Freeze coherent frontend/backend views of current runtime Value bindings."""

    semantic_bindings: Mapping[str, RuntimeBindingSymbol]
    backend_values: Mapping[BindingId, Value]

    def __post_init__(self):
        """Freeze both maps while retaining exact backend Value object identity."""
        object.__setattr__(self, "semantic_bindings", MappingProxyType(dict(self.semantic_bindings)))
        object.__setattr__(self, "backend_values", MappingProxyType(dict(self.backend_values)))


class _ResolvedEnvironmentSlot:
    """Resolve at most one immutable environment for a compiler-owned backend."""

    def __init__(self, resolved_environment: ResolvedEnvironment | None = None):
        """Optionally seed the slot for direct catalog operations."""
        self._environment = resolved_environment

    def get(self) -> ResolvedEnvironment:
        """Return the seeded snapshot or resolve and retain one complete result."""
        if self._environment is None:
            self._environment = resolve_environment()
        return self._environment


class _ResolvedEnvironmentBoundBackend(BlenderGroupBackend):
    """Expose compiler-session identity without changing physical backend behavior."""

    def __init__(self, *, populate_candidate, resolved_environment_for_session):
        """Bind one population callback and its environment provider."""
        super().__init__(populate_candidate=populate_candidate)
        self._resolved_environment_for_session = resolved_environment_for_session


class Compiler:
    """Compilation context for one Geometry Nodes group build."""

    def __init__(
        self,
        group,
        group_input,
        consts=None,
        local_functions=None,
        local_group_cache=None,
        backend_builtins=None,
        group_backend=None,
        generated_resource_transaction=None,
        imported_library_functions=None,
        helper_namespace=None,
        local_helper_transaction=None,
        function_group_cache=None,
        function_group_transaction=None,
        function_group_owner_scope=None,
        function_definition_owner=None,
        function_compilation_trace=None,
        reserved_name_labels=None,
        resolved_environment: ResolvedEnvironment | None = None,
    ):
        """Initialize state shared by expression, statement, and call compilers."""
        self.group = group
        self.group_input = group_input
        self.vars = {}
        self.consts = consts if consts is not None else {}
        self.local_functions = local_functions or {}
        self.local_group_cache = local_group_cache if local_group_cache is not None else {}
        self.function_group_cache = function_group_cache if function_group_cache is not None else self.local_group_cache
        self.backend_builtins = dict(backend_builtins or {})
        # RESOLVED_ENVIRONMENT_MIGRATION: Compiler is still exported and legacy tests or
        # integrations may construct it directly without the root compiler entry points.
        # A standalone Compiler creates one snapshot and an environment-bound backend, or
        # adopts the exact snapshot exposed by a supplied environment-bound backend. Reject
        # arbitrary backends because their nested populate callback could resolve again.
        # Production root entry points always pass one shared ResolvedEnvironment. Remove
        # this fallback when Compiler is internal/session-owned and every caller must pass
        # both an explicit environment and its matching session-bound backend.
        backend_environment = None
        if group_backend is not None:
            provider = getattr(group_backend, "_resolved_environment_for_session", None)
            if not callable(provider):
                raise CompileError(
                    "Internal error: supplied group backend is not bound to a resolved environment"
                )
            backend_environment = provider()

        if resolved_environment is None:
            resolved_environment = (
                backend_environment
                if backend_environment is not None
                else resolve_environment()
            )

        if group_backend is None:
            group_backend = _new_group_backend(resolved_environment)
        elif backend_environment is not resolved_environment:
            raise CompileError(
                "Internal error: supplied group backend uses a different resolved environment"
            )

        self.resolved_environment = resolved_environment
        self.group_backend = group_backend
        self.generated_resource_transaction = generated_resource_transaction
        self.imported_library_functions = dict(imported_library_functions or {})
        self.helper_namespace = helper_namespace or getattr(group, "name", "Group")
        self.local_helper_transaction = function_group_transaction or local_helper_transaction
        self.function_group_transaction = function_group_transaction or local_helper_transaction
        self.function_group_owner_scope = function_group_owner_scope or make_function_group_owner_scope("ROOT", getattr(group, "name", "Group"))
        self.function_definition_owner = function_definition_owner or self.function_group_owner_scope
        self.function_compilation_trace = function_compilation_trace or FunctionCompilationTrace()
        self.reserved_name_labels = dict(reserved_name_labels or {})
        self._function_occurrence_counts = {}
        self._runtime_binding_ids = {}
        self._next_runtime_binding_local_id = 0
        self._interface_inputs_by_identifier = {}
        self._interface_inputs_by_socket_pointer = {}
        self._panel_input_memberships = {}
        self._runtime_state_frames = []
        self.depth = 0

    def push_runtime_frame(self, frame):
        """Push one lexical Repeat Zone runtime-state frame."""
        self._runtime_state_frames.append(frame)

    def pop_runtime_frame(self, expected=None):
        """Pop the innermost lexical Repeat frame and optionally verify ownership."""
        if not self._runtime_state_frames:
            raise CompileError("Internal error: runtime Repeat frame stack is empty")
        frame = self._runtime_state_frames.pop()
        if expected is not None and frame is not expected:
            raise CompileError("Internal error: runtime Repeat frame stack ownership mismatch")
        return frame

    def replace_active_runtime_frame(self, frame):
        """Replace the innermost Repeat frame without changing lexical stack depth."""
        if not self._runtime_state_frames:
            raise CompileError("Internal error: no active runtime Repeat frame to replace")
        previous = self._runtime_state_frames[-1]
        self._runtime_state_frames[-1] = frame
        return previous

    def runtime_frame_for_builder(self, builder):
        """Return the nearest active Repeat frame and descriptor owning *builder*."""
        for frame in reversed(self._runtime_state_frames):
            descriptor = frame.descriptor_for_builder(builder)
            if descriptor is not None:
                return frame, descriptor
        return None, None

    def compile(self, expr):
        """Compile one AST expression into this group's node tree."""
        self.depth += 1
        try:
            return expression_compiler.compile_expr(self, expr, self.depth)
        finally:
            self.depth -= 1

    @staticmethod
    def _rna_pointer(value):
        """Return stable in-process identity for one Blender RNA wrapper."""
        try:
            return int(value.as_pointer())
        except Exception:
            return id(value)

    def _register_interface_input(self, socket, iface_item):
        """Associate one output of this compiler's Group Input with its interface item."""
        if socket is None or iface_item is None:
            raise CompileError("Internal error: cannot register a missing group input socket")
        owner = getattr(socket, "node", None)
        if self._rna_pointer(owner) != self._rna_pointer(self.group_input):
            raise CompileError("Internal error: interface input socket is owned by another node")
        identifier = getattr(socket, "identifier", None)
        if identifier:
            self._interface_inputs_by_identifier[identifier] = iface_item
        self._interface_inputs_by_socket_pointer[self._rna_pointer(socket)] = iface_item

    def interface_input_for_value(self, value):
        """Resolve a Value only when it is an output of this compiler's exact Group Input."""
        if not isinstance(value, Value):
            return None
        socket = getattr(value, "socket", None)
        if socket is None:
            return None
        owner = getattr(socket, "node", None)
        if self._rna_pointer(owner) != self._rna_pointer(self.group_input):
            return None
        identifier = getattr(socket, "identifier", None)
        if identifier:
            iface_item = self._interface_inputs_by_identifier.get(identifier)
            if iface_item is not None:
                return iface_item
        return self._interface_inputs_by_socket_pointer.get(self._rna_pointer(socket))

    def interface_input_identity_for_value(self, value):
        """Return current-group panel membership identity after proving socket ownership."""
        iface_item = self.interface_input_for_value(value)
        if iface_item is None:
            return None
        identifier = getattr(iface_item, "identifier", None)
        if identifier:
            return ("identifier", identifier)
        return ("interface", self._rna_pointer(iface_item))

    def runtime_binding_id(self, name: str) -> BindingId:
        """Return the stable compiler-local identity for one source binding slot."""
        binding_id = self._runtime_binding_ids.get(name)
        if binding_id is None:
            binding_id = BindingId(self.function_group_owner_scope, self._next_runtime_binding_local_id)
            self._next_runtime_binding_local_id += 1
            self._runtime_binding_ids[name] = binding_id
        return binding_id

    def snapshot_runtime_bindings(self) -> RuntimeBindingSnapshot:
        """Snapshot current runtime Values into coherent semantic/backend identity maps."""
        semantic_bindings = {}
        backend_values = {}
        # CANONICAL_BINDING_ID_MIGRATION: comp.vars remains the legacy heterogeneous
        # source-name store while statement, loop, call, and compile-time binding migration
        # is incomplete. Project current Value entries into stable BindingId-based semantic
        # and backend snapshots here without changing comp.vars ownership. Remove this bridge
        # when runtime bindings are stored canonically by BindingId and source names exist
        # only in the frontend symbol table.
        for name, value in self.vars.items():
            if not isinstance(value, Value):
                continue
            binding_id = self.runtime_binding_id(name)
            semantic_bindings[name] = RuntimeBindingSymbol(binding_id, value.typ)
            backend_values[binding_id] = value
        return RuntimeBindingSnapshot(semantic_bindings, backend_values)

    def resolve_reusable_function_materialization(
        self,
        function_id: FunctionId,
        modifiers: FunctionCallModifiers,
    ) -> IRFunctionMaterialization:
        """Resolve shared/unique semantics for one canonical reusable callable."""
        if not isinstance(function_id, FunctionId):
            raise TypeError("function_id must be a FunctionId")
        if not isinstance(modifiers, FunctionCallModifiers):
            raise TypeError("modifiers must be FunctionCallModifiers")
        if not modifiers.unique:
            return IRFunctionMaterialization(
                function_id,
                IRFunctionMaterializationMode.SHARED,
                None,
            )
        # REUSABLE_CALL_IR_MIGRATION: Unique materialization is now represented explicitly
        # by IRFunctionMaterialization, but its CallSiteId ordinal is still allocated when
        # the legacy AST call path reaches reusable-call preparation. Preserve the current
        # unique-only per-owner/per-callee sequence here. Remove this allocator bridge when
        # reusable calls are emitted by semantic lowering before backend/materialization.
        counter_key = (self.function_group_owner_scope, function_id)
        ordinal = self._function_occurrence_counts.get(counter_key, 0)
        self._function_occurrence_counts[counter_key] = ordinal + 1
        call_site = CallSiteId(self.function_group_owner_scope, function_id, ordinal)
        return IRFunctionMaterialization(
            function_id,
            IRFunctionMaterializationMode.UNIQUE,
            call_site,
        )

    def _compile_const_value(self, value, x=0, y=0):
        """Turn a compile-time constant into a node Value or script-level array."""
        if _is_const_vector(value) or (
            isinstance(value, (tuple, list))
            and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        ):
            return _combine_xyz_mixed(self.group, list(value), x, y)
        if isinstance(value, bool):
            val = _value(self.group, 1.0 if value else 0.0, x, y)
            zero = _value(self.group, 0.0, x + 20, y - 40)
            return _compare(self.group, "NOT_EQUAL", val, zero, x, y)
        if isinstance(value, int):
            return _value(self.group, value, x, y)
        if isinstance(value, float):
            return _value(self.group, value, x, y)
        if isinstance(value, str):
            return _string_value(self.group, value, x, y)
        if isinstance(value, (list, tuple)):
            return [self._compile_const_value(v, x, y) for v in value]
        raise CompileError("Unsupported compile-time value in runtime expression")

    def _create_input_socket_value(self, name, typ, default=None):
        """Create or reuse a group input socket and expose it as a Value."""
        if name in self.vars:
            existing = self.vars[name]
            if existing.typ != typ:
                raise CompileError(f'Input "{name}" already exists with another type')
            return existing
        sock_type = _socket_type_for(typ)
        iface = self.group.interface.new_socket(name=name, in_out="INPUT", socket_type=sock_type)
        if default is not None:
            _set_socket_default(iface, default)
            _set_interface_socket_default(self.group, name, "INPUT", default)
            _record_group_input_default(self.group, name, typ, default)
        socket = next((s for s in self.group_input.outputs if s.name == name), None)
        if socket is None:
            raise CompileError(f'Internal error: input socket "{name}" was not created')
        self._register_interface_input(socket, iface)
        val = make_value(socket, typ)
        self.vars[name] = val
        return val

    def _const_eval_macro_arg(self, expr):
        """Evaluate a compile-time macro argument."""
        return _const_eval(expr, self.consts)

    def _const_or_compile_arg(self, expr, depth=0):
        """Return a compile-time value or a dynamic Value for a call argument."""
        try:
            return _const_eval(expr, self.consts), False
        except CompileError:
            return self.compile(expr), True


def _validate_import_bindings(
    import_pairs,
    body_stmts,
    local_function_defs,
    backend_names,
    resolved_environment,
    inherited_imports=None,
):
    """Validate and return source-local namespace-aware catalog bindings."""
    imported: dict[str, LibraryBinding] = {}
    local_bindings = _binding_names(body_stmts)
    reserved_names = set(builtin_registry.BUILTIN_NAMES) | {"output", "store", "panel"} | set(_ALLOWED_CONSTS) | set(resolved_environment.system_names()) | set(backend_names) | set(TYPE_TOKEN_NAMES)

    def validate_pair(namespace, canonical_name, exposed_name, *, inherited=False, inherited_record=None):
        catalog = resolved_environment.catalog(namespace)
        record = catalog.find(canonical_name)
        if record is None:
            raise CompileError(f"Unknown {namespace} import: {canonical_name}")
        if inherited_record is not None and inherited_record is not record:
            raise CompileError("Internal error: inherited library binding does not match resolved environment")
        binding = LibraryBinding(namespace, canonical_name, record)
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
            continue
        validate_pair(namespace, import_request.canonical_name, import_request.exposed_name)
    for inherited_exposed, inherited_binding in dict(inherited_imports or {}).items():
        if isinstance(inherited_binding, LibraryBinding):
            validate_pair(
                inherited_binding.namespace,
                inherited_binding.canonical_name,
                inherited_exposed,
                inherited=True,
                inherited_record=inherited_binding.record,
            )
        else:
            validate_pair("functions", inherited_binding, inherited_exposed, inherited=True)
    return imported


def _registered_name_labels(local_function_defs, backend_names, imported_library_functions, system_names):
    """Return active DSL-owned names and human-readable reservation labels."""
    labels = {}

    def add(names, label):
        for name in names:
            labels.setdefault(name, label)

    add(builtin_registry.BUILTIN_NAMES, "DSL builtin")
    add({"output", "store", "panel"}, "reserved helper")
    add(_ALLOWED_CONSTS, "compile-time constant")
    add(system_names, "embedded-system constructor")
    add(backend_names, "backend helper")
    add(TYPE_TOKEN_NAMES, "type token")
    add(imported_library_functions, "imported function")
    add(local_function_defs, "local function")
    return labels


def _format_reserved_label(label):
    """Format a reservation label for diagnostics."""
    if label == "DSL builtin":
        return "reserved by DSL builtin"
    if label == "imported function":
        return "already registered as imported function"
    if label == "local function":
        return "already registered as local function"
    if label == "type token":
        return "reserved by type token"
    return f"reserved by {label}"


def _allows_existing_top_level_shadow(label):
    """Return True for legacy top-level names that remain value-rebindable."""
    return label in {"DSL builtin", "compile-time constant"}


def _check_registered_binding(name, labels, *, context="assign", allow_existing_shadow=False):
    """Reject binding to a name that is already owned by non-shadowable DSL behavior."""
    label = labels.get(name)
    if label is None:
        return
    if allow_existing_shadow and _allows_existing_top_level_shadow(label):
        return
    if context == "parameter":
        raise CompileError(f"Local function parameter {name} is {_format_reserved_label(label)}")
    raise CompileError(f"Cannot assign to {name}: name is {_format_reserved_label(label)}")


def _validate_registered_name_bindings(stmts, labels, *, top_level_function_names=None):
    """Validate raw AST binding sites before compile-time preprocessing can erase them."""
    top_level_function_names = set(top_level_function_names or ())

    def check_target(target, *, allow_existing_shadow=False):
        if isinstance(target, ast.Name):
            _check_registered_binding(target.id, labels, allow_existing_shadow=allow_existing_shadow)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                check_target(item, allow_existing_shadow=allow_existing_shadow)

    def visit(stmt, *, top_level=False, in_local_function=False):
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
    """Allow panel() only as a direct expression in the immediate group body."""

    def is_panel_call(node):
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "panel"

    for stmt in stmts:
        direct_call = stmt.value if isinstance(stmt, ast.Expr) and is_panel_call(stmt.value) else None
        for node in ast.walk(stmt):
            if is_panel_call(node) and node is not direct_call:
                raise CompileError("panel() is a top-level interface declaration")


def _populate_group(
    group,
    source: str,
    name: str = "NodeForge Group",
    *,
    resolved_environment_for_session,
    local_functions=None,
    backend_builtins=None,
    generated_resource_transaction=None,
    imported_library_functions=None,
    helper_namespace=None,
    local_helper_transaction=None,
    function_group_cache=None,
    function_group_transaction=None,
    function_group_owner_scope=None,
    function_definition_owner=None,
    function_compilation_trace=None,
    function_compilation_inputs=None,
    function_definition_identity=None,
    function_instance_key=None,
    root_owner_id=None,
    group_backend=None,
):
    """Compile NodeForge source into an already-created fresh GeometryNodeTree."""
    raw_stmts = _parse_source(source)
    resolved_environment = resolved_environment_for_session()
    raw_body_stmts, import_pairs = _extract_function_imports(raw_stmts)
    system_names = resolved_environment.system_names()

    local_function_defs = dict(local_functions or {})
    for existing_local_name in local_function_defs:
        if existing_local_name in system_names:
            raise CompileError(
                f"Local function {existing_local_name!r} collides with reserved system constructor name"
            )
    body_stmts = []
    for stmt in raw_body_stmts:
        if isinstance(stmt, ast.FunctionDef):
            if stmt.name in system_names:
                raise CompileError(
                    f"Local function {stmt.name!r} collides with reserved system constructor name"
                )
            if stmt.name in local_function_defs:
                raise CompileError(f"Duplicate local function: {stmt.name}")
            local_function_defs[stmt.name] = stmt
        else:
            body_stmts.append(stmt)

    backend_names = set(backend_builtins or {})
    for helper_name in backend_names:
        if helper_name in system_names:
            raise CompileError(
                f"Local backend helper {helper_name!r} collides with reserved system constructor name"
            )
    # Preserve the existing invariant that bundled catalog entries cannot use
    # reserved embedded-system names even when the current source has no imports.
    for namespace in ("functions", "examples"):
        resolved_environment.catalog(namespace).names()
    own_imported_library_functions = _validate_import_bindings(
        import_pairs,
        raw_body_stmts,
        local_function_defs,
        backend_names,
        resolved_environment,
        inherited_imports=imported_library_functions,
    )
    reserved_name_labels = _registered_name_labels(
        local_function_defs,
        backend_names,
        own_imported_library_functions,
        system_names,
    )
    _validate_registered_name_bindings(
        raw_body_stmts,
        reserved_name_labels,
        top_level_function_names=local_function_defs,
    )
    _validate_interface_directive_placement(raw_body_stmts)

    stmts, consts = _preprocess_compile_time(body_stmts)
    callable_names = set(own_imported_library_functions) | set(local_function_defs) | backend_names | set(system_names)
    input_names = sorted(set(_collect_inputs(stmts, extra_builtin_names=callable_names, consts=consts)) - set(consts.keys()))
    input_types = _infer_input_types(stmts)
    try:
        group.color_tag = 'CONVERTER'
    except Exception:
        pass
    if root_owner_id is not None:
        group[FUNCTION_ROOT_OWNER_ID_PROP] = root_owner_id
    _store_group_source(group, source)
    try:
        group[INPUT_DEFAULTS_PROP] = {}
    except Exception:
        pass

    geometry_mode = _needs_geometry_io(stmts)
    if geometry_mode:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for input_name in input_names:
        sock_type = "NodeSocketInt" if input_types.get(input_name) == TYPE_INT else "NodeSocketFloat"
        sock = group.interface.new_socket(name=input_name, in_out="INPUT", socket_type=sock_type)
        default_value = (1 if input_name == "iterations" else 0) if sock_type == "NodeSocketInt" else 0.0
        _set_socket_default(sock, default_value)
        _set_interface_socket_default(group, input_name, "INPUT", default_value)
        _record_group_input_default(group, input_name, input_types.get(input_name, TYPE_FLOAT), default_value)

    group_input = _new_node(group, "NodeGroupInput", -1100, 0)
    group_output = _new_node(group, "NodeGroupOutput", 1100, 0)
    group_output.is_active_output = True
    comp = Compiler(
        group,
        group_input,
        consts,
        local_functions=local_function_defs,
        local_group_cache=function_group_cache if function_group_cache is not None else {},
        backend_builtins=backend_builtins,
        group_backend=group_backend,
        generated_resource_transaction=generated_resource_transaction,
        imported_library_functions=own_imported_library_functions,
        helper_namespace=helper_namespace or name,
        local_helper_transaction=local_helper_transaction,
        function_group_cache=function_group_cache,
        function_group_transaction=function_group_transaction or local_helper_transaction,
        function_group_owner_scope=function_group_owner_scope,
        function_definition_owner=function_definition_owner,
        function_compilation_trace=function_compilation_trace,
        reserved_name_labels=reserved_name_labels,
        resolved_environment=resolved_environment,
    )

    implicit_iface_by_identifier = {
        getattr(item, "identifier", None): item
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "INPUT"
    }
    for socket in group_input.outputs:
        if socket.name in input_names:
            iface_item = implicit_iface_by_identifier.get(getattr(socket, "identifier", None))
            if iface_item is None:
                iface_item = next(
                    (
                        item for item in group.interface.items_tree
                        if getattr(item, "item_type", None) == "SOCKET"
                        and getattr(item, "in_out", None) == "INPUT"
                        and getattr(item, "name", None) == socket.name
                    ),
                    None,
                )
            if iface_item is None:
                raise CompileError(f'Internal error: implicit input socket "{socket.name}" has no interface item')
            comp._register_interface_input(socket, iface_item)
            comp.vars[socket.name] = make_value(socket, input_types.get(socket.name, TYPE_FLOAT))

    geometry_socket = None
    if geometry_mode:
        geometry_socket = next((s for s in group_input.outputs if s.name == "Geometry"), None)
        if geometry_socket is None:
            raise CompileError("Internal error: missing Geometry input")

    ctx = GroupBuildContext(
        group=group,
        comp=comp,
        consts=comp.consts,
        geometry_mode=geometry_mode,
        geometry_socket=geometry_socket,
    )
    frame = None
    if function_compilation_inputs is not None and function_compilation_trace is not None:
        own_inputs = dict(function_compilation_inputs)
        own_inputs["lowered_source"] = normalized_statements(stmts)
        trace_cm = function_compilation_trace.group(function_definition_identity or function_group_owner_scope or group.name, own_inputs)
        frame = trace_cm.__enter__()
    try:
        compile_statements(ctx, stmts)
    finally:
        if frame is not None:
            trace_cm.__exit__(None, None, None)

    if geometry_mode:
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group.links.new(ctx.geometry_socket, group_output.inputs["Geometry"])

    if ctx.explicit_outputs:
        outputs = ctx.explicit_outputs
    elif ctx.auto_final_output is not None:
        outputs = [ctx.auto_final_output]
    else:
        outputs = []

    used_interface_names = {"Geometry"} if geometry_mode else set()
    for output_name, result in outputs:
        if isinstance(result, list):
            raise CompileError("Cannot output an array directly; use join(array) or index it")
        reject_compile_time_object(result, "final output")
        final_name = _unique_output_name(used_interface_names, output_name)
        group.interface.new_socket(name=final_name, in_out="OUTPUT", socket_type=_socket_type_for(result.typ))
        group.links.new(result.socket, group_output.inputs[final_name])

    if not geometry_mode and not outputs:
        raise CompileError("Script produced no output. Use out = ..., output(...), set_position(...), or store(...)")

    if function_compilation_inputs is not None and function_compilation_trace is not None:
        contract = interface_contract(group)
        result = frame.finish(contract)
        if not result.freshness_unproven:
            stamp_function_metadata(
                group,
                instance_key=function_instance_key,
                definition_owner=function_definition_owner,
                fingerprint=result.fingerprint,
            )
    return group


def _new_group_backend(resolved_environment: ResolvedEnvironment | None = None):
    """Return a physical backend bound to one compiler-session environment slot."""
    slot = _ResolvedEnvironmentSlot(resolved_environment)
    populate = functools.partial(
        _populate_group,
        resolved_environment_for_session=slot.get,
    )
    return _ResolvedEnvironmentBoundBackend(
        populate_candidate=populate,
        resolved_environment_for_session=slot.get,
    )


def create_expression_group(source: str, name: str = "NodeForge Group"):
    """Create a new Geometry Nodes group from NodeForge source."""
    return _new_group_backend().create_or_update(source=source, name=name)


def create_library_catalog_group(namespace: str, name: str):
    """Create or update a reusable node group for a catalog entry."""
    environment = resolve_environment()
    record = environment.catalog(namespace).find(name)
    if record is None:
        raise CompileError(f"Unknown {namespace} library entry: {name}")
    return materialize_library_entry_group_for_record(
        record,
        _new_group_backend(environment),
    )


def create_library_function_group(name: str):
    """Create or update a reusable node group for a function-library entry."""
    return create_library_catalog_group("functions", name)


def update_library_catalog_group(group, namespace: str, name: str):
    """Reload a catalog-backed group in place from its current editable source."""
    environment = resolve_environment()
    record = environment.catalog(namespace).find(name)
    if record is None:
        raise CompileError(f"Current source for {namespace} library entry {name!r} is unavailable")
    return update_materialized_library_entry_group_for_record(
        record,
        group,
        _new_group_backend(environment),
    )


def update_expression_group(group, source: str):
    """Rebuild an existing Geometry Nodes group from NodeForge source."""
    return _new_group_backend().create_or_update(source=source, name=getattr(group, "name", "NodeForge Group"), existing_group=group)


__all__ = [
    "CompileError",
    "Compiler",
    "RuntimeBindingSnapshot",
    "FunctionGroupBuildTransaction",
    "LocalHelperBuildTransaction",
    "create_expression_group",
    "update_expression_group",
    "update_library_catalog_group",
    "create_library_catalog_group",
    "create_library_function_group",
    "_apply_group_defaults_to_node",
    "_capture_node_external_state",
    "_restore_node_external_state",
    "_capture_group_external_state",
    "_restore_group_external_state",
    "_extract_group_source",
    "_get_or_create_scratch_text",
    "_replace_text_contents",
]
