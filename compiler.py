"""Public compiler facade and high-level Geometry Nodes group assembly."""

import ast
from contextlib import nullcontext
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import bpy

from .constants import TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_TOKEN_NAMES, _ALLOWED_CONSTS
from .nf_types import NFType
from .errors import CompileError
from .compiler_identities import BindingId, GroupCompilationIdentity
from .values import TupleValue, Value, make_value
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
from .interface import _set_socket_default, _set_interface_socket_default, _record_group_input_default, _create_group_input_socket
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
from .compile_time import CompileTimeObject, CompileTimeState, reject_compile_time_object
from . import expression_compiler
from .builtins import registry as builtin_registry
from .function_instances import (
    FUNCTION_ROOT_OWNER_ID_PROP,
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
from .semantic_group import LibraryBinding, SemanticGroupCompilation, analyze_group_source
from .source_callables import SourceCallableSession
from .blender_ir_lowering import BlenderIRLoweringContext, lower_body
from .function_materializer import FunctionMaterializationContext, FunctionMaterializer
from .group_context import GroupContextSlot
from .callable_contracts import canonicalize_group_input_default
from .runtime_bindings import FrontendRuntimeBindings, RuntimeBindingSymbol
from .blender_group_backend import BlenderGroupBackend, BlenderGroupBuildRequest, BlenderGroupBuildTransaction
from .resolved_environment import ResolvedEnvironment, resolve_environment

# BLENDER_TRANSACTION_LEGACY_ALIAS_MIGRATION: Keep the historical transaction class
# names importable from compiler.py for one compatibility phase after physical Blender
# transaction ownership moves to blender_group_backend.py. New production code must
# import BlenderGroupBuildTransaction from the Blender group backend module and must not
# depend on these aliases. Remove them after the next compatibility sweep confirms no
# supported external/test consumer imports FunctionGroupBuildTransaction or
# LocalHelperBuildTransaction from compiler.py.
FunctionGroupBuildTransaction = BlenderGroupBuildTransaction
LocalHelperBuildTransaction = BlenderGroupBuildTransaction



@dataclass(frozen=True)
class _CompilerBindingState:
    """Shallow coherent snapshot of active compiler binding maps."""

    runtime_symbols: Mapping[str, RuntimeBindingSymbol]
    runtime_values: Mapping[BindingId, Value]
    legacy_structural: Mapping[str, object]

    def __post_init__(self):
        """Detach the three maps while preserving contained object identity."""
        object.__setattr__(self, "runtime_symbols", MappingProxyType(dict(self.runtime_symbols)))
        object.__setattr__(self, "runtime_values", MappingProxyType(dict(self.runtime_values)))
        object.__setattr__(self, "legacy_structural", MappingProxyType(dict(self.legacy_structural)))


@dataclass(frozen=True)
class _CompilerNameBindingState:
    """Capture one source name for temporary lexical shadowing and restoration."""

    runtime_symbol: RuntimeBindingSymbol | None
    runtime_value: Value | None
    legacy_structural: object | None
    had_legacy_structural: bool


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
    """Bind one immutable environment to semantic preparation and physical publication."""

    def __init__(self, *, populate_candidate, prepare_compilation, resolved_environment_for_session):
        """Bind population, preparation, and one compiler-session environment provider."""
        super().__init__(populate_candidate=populate_candidate, prepare_compilation=prepare_compilation)
        self._resolved_environment_for_session = resolved_environment_for_session

    def new_source_callable_session(self):
        """Create one root-attempt source/preparation session over the bound environment."""
        return SourceCallableSession(resolved_environment=self._resolved_environment_for_session())

    def compile_group_callback(self, source: str, name: str = "NodeForge Group", **kwargs):
        """Ensure public package builds share one source-call session across both phases."""
        kwargs.setdefault("source_callable_session", self.new_source_callable_session())
        return super().compile_group_callback(source, name=name, **kwargs)


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
        input_declaration_owner=None,
        function_compilation_trace=None,
        reserved_name_labels=None,
        resolved_environment: ResolvedEnvironment | None = None,
    ):
        """Initialize state shared by expression, statement, and call compilers."""
        self.group = group
        self.group_input = group_input
        if isinstance(consts, CompileTimeState):
            self.compile_time = consts
        else:
            backing_consts = consts if consts is not None else {}
            self.compile_time = CompileTimeState(backing_consts, adopt_mapping=True)
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
        self.input_declaration_owner = input_declaration_owner or self.function_definition_owner
        self.function_compilation_trace = function_compilation_trace or FunctionCompilationTrace()
        self.reserved_name_labels = dict(reserved_name_labels or {})
        self._runtime_bindings = FrontendRuntimeBindings(self.function_group_owner_scope)
        # BASIC_BODY_IR_LEGACY_BACKEND_BINDING_BRIDGE: IRBody lowering owns values created by
        # inter-statement assignments, while body-entry input/state seeding and retained legacy helpers still
        # require compiler-session BindingId -> Value materializations. Never publish IRBody-created local
        # assignment Values back into this map. Remove this bridge when all supported body-entry/stateful
        # publication passes backend materializations directly into body lowering and legacy helpers are removed.
        self._runtime_binding_values: dict[BindingId, Value] = {}
        # LEGACY_STRUCTURAL_BINDINGS_COMPAT: Semantic Body owns structural arrays and GeometryBuilder
        # through Blender-independent frontend identity/state and never publishes them here. This backend
        # container store remains only for the frozen legacy implementation and direct characterization;
        # production root compilation must not read or write it.
        self._legacy_structural_bindings: dict[str, object] = {}
        # CONTEXTUAL_GROUP_LEGACY_PANEL_SOCKET_MAP_COMPAT: Semantic panel() ownership uses frontend
        # InterfaceInputOrigin provenance and IRPanelDeclaration rather than Blender socket identity. These
        # lookup/membership maps remain only with the frozen legacy panel implementation for characterization
        # and pending deletion; production root compilation must not depend on them.
        self._interface_inputs_by_identifier = {}
        self._interface_inputs_by_socket_pointer = {}
        self._panel_input_memberships = {}
        # CONTROL_FLOW_IR_LEGACY_RUNTIME_FRAME_COMPAT: IRIf/IRRepeat, including frontend-owned
        # GeometryBuilder state, use semantic BindingIds and recursive Blender IR lowering and never read or
        # mutate this Compiler frame stack. The stack remains only with the frozen legacy runtime implementation
        # for direct characterization and pending deletion.
        self._runtime_state_frames = []
        # CONTEXTUAL_GROUP_LEGACY_GRID_EXPRESSION_ROUTE_COMPAT: This flag belongs only to the frozen
        # legacy expression implementation so direct characterization can reproduce historical grid()/grid_uv()
        # behavior. Production root compilation never enables it; remove it with the retained legacy grid path.
        self._legacy_contextual_grid_expression_routing_active = False
        self.depth = 0

    # COMPILE_TIME_STATE_COMPILER_CONSTS_COMPAT: Compile-time bindings are now owned by
    # Compiler.compile_time, but Compiler remains importable and older tests/integrations may read,
    # mutate, or replace the historical mutable comp.consts mapping directly. Keep this facade backed
    # by the exact CompileTimeState mapping so those direct Compiler callers retain current behavior.
    # Repository-owned production code must not use comp.consts. Remove this facade when Compiler is
    # internal/session-owned and no supported integration or test contract mutates .consts directly.
    @property
    def consts(self):
        """Return the historical mutable compile-time mapping compatibility surface."""
        return self.compile_time._values

    @consts.setter
    def consts(self, values):
        """Replace historical compile-time mapping contents without replacing the owner."""
        if hasattr(self, "compile_time"):
            self.compile_time.replace(values)
        else:
            self.compile_time = CompileTimeState(values if values is not None else {}, adopt_mapping=True)

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
            # TODO(nodeforge-migration): Compiler.compile() is retained only for direct legacy-expression
            # characterization while the old expression compiler still exists. Supported source-backed and
            # Python-extension production calls must use the permanent semantic/IR pipeline. Remove this method
            # and marker when the retained legacy expression compiler and its characterization-only entry point
            # are deleted.
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

    def runtime_binding(self, name: str) -> RuntimeBindingSymbol | None:
        """Return active frontend metadata for one ordinary runtime source binding."""
        return self._runtime_bindings.get(name)

    def runtime_bindings_snapshot(self) -> Mapping[str, RuntimeBindingSymbol]:
        """Return a detached read-only snapshot of active frontend runtime bindings."""
        return self._runtime_bindings.snapshot()

    def runtime_value(self, name: str) -> Value | None:
        """Return the current backend materialization for an active runtime source binding."""
        symbol = self._runtime_bindings.get(name)
        if symbol is None:
            return None
        value = self._runtime_binding_values.get(symbol.binding_id)
        if value is None:
            raise CompileError(f"Internal error: runtime binding {name!r} has no backend materialization")
        if value.typ is not symbol.typ:
            raise CompileError(f"Internal error: runtime binding {name!r} type/materialization mismatch")
        return value

    def backend_runtime_values_snapshot(self) -> Mapping[BindingId, Value]:
        """Return a detached read-only snapshot of current backend runtime materializations."""
        self._validate_binding_state()
        return MappingProxyType(dict(self._runtime_binding_values))

    def bind_runtime_value(self, name: str, value: Value) -> RuntimeBindingSymbol:
        """Atomically publish one ordinary runtime Value under compiler-owned frontend identity."""
        if not isinstance(value, Value):
            raise TypeError("runtime binding value must be a Value")
        if not isinstance(value.typ, NFType):
            raise TypeError("runtime binding Value.typ must be an NFType")
        self._validate_binding_state()
        symbol = self._runtime_bindings.bind(name, value.typ)
        self._runtime_binding_values[symbol.binding_id] = value
        self._legacy_structural_bindings.pop(name, None)
        self._validate_binding_state()
        return symbol

    def unbind_runtime_binding(self, name: str) -> None:
        """Deactivate one runtime binding while retaining its historical BindingId reservation."""
        symbol = self._runtime_bindings.get(name)
        if symbol is not None:
            self._runtime_binding_values.pop(symbol.binding_id, None)
            self._runtime_bindings.unbind(name)

    def legacy_structural_binding(self, name: str):
        """Return one remaining legacy-only array/builder/dynamic structural binding."""
        return self._legacy_structural_bindings.get(name)

    def has_legacy_structural_binding(self, name: str) -> bool:
        """Return whether a source name owns a remaining legacy-only structural binding."""
        return name in self._legacy_structural_bindings

    def legacy_structural_binding_names_snapshot(self) -> frozenset[str]:
        """Return only remaining legacy structural names without exposing their objects."""
        return frozenset(self._legacy_structural_bindings)

    @staticmethod
    def _validate_legacy_structural_candidate(value) -> None:
        """Accept only legacy array/builder/dynamic-result containers, never IRBody structure."""
        if isinstance(value, Value):
            raise TypeError("legacy structural binding cannot contain Value/ObjectValue")
        if not isinstance(value, (CompileTimeObject, list, TupleValue)):
            raise TypeError(f"unsupported legacy structural binding category: {type(value).__name__}")

    def bind_legacy_structural(self, name: str, value) -> None:
        """Publish one remaining legacy-only structural binding and deactivate runtime ownership."""
        if not isinstance(name, str) or not name:
            raise ValueError("legacy structural binding name must be a non-empty string")
        self._validate_legacy_structural_candidate(value)
        self._validate_binding_state()
        self.unbind_runtime_binding(name)
        self._legacy_structural_bindings[name] = value
        self._validate_binding_state()

    def unbind_legacy_structural(self, name: str) -> None:
        """Remove one active legacy structural binding."""
        self._legacy_structural_bindings.pop(name, None)

    def _validate_binding_state_candidate(self, runtime_symbols, runtime_values, legacy_structural) -> None:
        """Validate a complete candidate binding state without mutating current state."""
        self._runtime_bindings.validate_active_state(runtime_symbols)
        if not isinstance(runtime_values, Mapping) or not isinstance(legacy_structural, Mapping):
            raise TypeError("compiler binding state maps are invalid")
        active_ids = set()
        for name, symbol in runtime_symbols.items():
            if name in legacy_structural:
                raise CompileError(f"Internal error: binding {name!r} is both runtime and structural")
            value = runtime_values.get(symbol.binding_id)
            if not isinstance(value, Value):
                raise CompileError(f"Internal error: runtime binding {name!r} has no backend Value")
            if value.typ is not symbol.typ:
                raise CompileError(f"Internal error: runtime binding {name!r} type/materialization mismatch")
            active_ids.add(symbol.binding_id)
        if set(runtime_values) != active_ids:
            raise CompileError("Internal error: backend runtime binding map does not match active frontend bindings")
        if not all(isinstance(key, BindingId) for key in runtime_values):
            raise CompileError("Internal error: backend runtime binding map contains a non-BindingId key")
        for value in legacy_structural.values():
            self._validate_legacy_structural_candidate(value)

    def _validate_binding_state(self) -> None:
        """Verify active frontend, backend, and structural binding ownership is coherent."""
        self._validate_binding_state_candidate(
            self._runtime_bindings.snapshot(),
            self._runtime_binding_values,
            self._legacy_structural_bindings,
        )

    # COMPILE_TIME_STATE_LEGACY_BINDING_CHECKPOINT_COMPAT: The frozen legacy statement/runtime
    # implementation snapshots runtime symbols, backend Value materializations, and legacy structural bindings
    # while compiling speculative branches/loops. Production root compilation does not use these checkpoints;
    # remove them with the retained legacy implementation.
    def _snapshot_binding_state(self) -> _CompilerBindingState:
        """Capture a detached shallow checkpoint of all active binding maps."""
        self._validate_binding_state()
        return _CompilerBindingState(
            self._runtime_bindings.snapshot(),
            self._runtime_binding_values,
            self._legacy_structural_bindings,
        )

    def _restore_binding_state(self, state: _CompilerBindingState) -> None:
        """Restore one validated checkpoint without rewinding historical BindingId allocation."""
        if not isinstance(state, _CompilerBindingState):
            raise TypeError("state must be a _CompilerBindingState")
        self._validate_binding_state_candidate(
            state.runtime_symbols, state.runtime_values, state.legacy_structural
        )
        self._runtime_bindings.restore_active_state(state.runtime_symbols)
        self._runtime_binding_values = dict(state.runtime_values)
        self._legacy_structural_bindings = dict(state.legacy_structural)
        self._validate_binding_state()

    def _snapshot_binding_name_state(self, name: str) -> _CompilerNameBindingState:
        """Capture one source name for temporary lexical shadowing."""
        symbol = self.runtime_binding(name)
        value = self.runtime_value(name) if symbol is not None else None
        had_structural = self.has_legacy_structural_binding(name)
        structural = self.legacy_structural_binding(name) if had_structural else None
        return _CompilerNameBindingState(symbol, value, structural, had_structural)

    def _restore_binding_name_state(self, name: str, state: _CompilerNameBindingState) -> None:
        """Restore one source name while retaining all historical BindingId reservations."""
        if not isinstance(state, _CompilerNameBindingState):
            raise TypeError("state must be a _CompilerNameBindingState")
        if state.runtime_symbol is not None:
            if state.runtime_value is None or state.runtime_value.typ is not state.runtime_symbol.typ:
                raise CompileError("Internal error: invalid saved runtime binding state")
            self._runtime_bindings.validate_active_state({name: state.runtime_symbol})
        elif state.had_legacy_structural:
            self._validate_legacy_structural_candidate(state.legacy_structural)
        self.unbind_runtime_binding(name)
        self.unbind_legacy_structural(name)
        if state.runtime_symbol is not None:
            restored = self._runtime_bindings.bind(name, state.runtime_symbol.typ)
            self._runtime_binding_values[restored.binding_id] = state.runtime_value
        elif state.had_legacy_structural:
            self._legacy_structural_bindings[name] = state.legacy_structural
        self._validate_binding_state()

    def _binding_identity_view(self, state: _CompilerBindingState | None = None) -> Mapping[str, object]:
        """Return source names mapped to current backend/structural object identity for branch diffing."""
        if state is None:
            state = self._snapshot_binding_state()
        view = {}
        for name, symbol in state.runtime_symbols.items():
            view[name] = state.runtime_values[symbol.binding_id]
        view.update(state.legacy_structural)
        return MappingProxyType(view)

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

    def _create_input_socket_value(self, name, typ, default=None, *, declaration_id=None):
        """Create one fresh explicit group input and expose it as a runtime Value."""
        value, iface = _create_group_input_socket(
            self.group,
            self.group_input,
            name,
            typ,
            default,
            declaration_id=declaration_id,
        )
        self._register_interface_input(value.socket, iface)
        return value

    def _const_eval_macro_arg(self, expr):
        """Evaluate a compile-time macro argument."""
        return _const_eval(expr, self.compile_time.values)

    def _const_or_compile_arg(self, expr, depth=0):
        """Return a compile-time value or a dynamic Value for a call argument."""
        try:
            return _const_eval(expr, self.compile_time.values), False
        except CompileError:
            return self.compile(expr), True




def _assert_prepared_interface_parity(group, prepared: SemanticGroupCompilation) -> None:
    """Assert realized public sockets match the frontend-owned final callable contract."""
    physical_inputs = [
        item for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "INPUT"
    ]
    physical_outputs = [
        item for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "OUTPUT"
    ]
    if len(physical_inputs) != len(prepared.interface.inputs):
        raise CompileError("Internal error: physical input count disagrees with semantic interface")
    if len(physical_outputs) != len(prepared.interface.outputs):
        raise CompileError("Internal error: physical output count disagrees with semantic interface")
    for physical, contract in zip(physical_inputs, prepared.interface.inputs):
        if getattr(physical, "name", "") != contract.display_name:
            raise CompileError("Internal error: physical input order/name disagrees with semantic interface")
        socket_type = getattr(physical, "socket_type", "") or getattr(physical, "bl_socket_idname", "")
        if socket_type != _socket_type_for(contract.typ):
            raise CompileError("Internal error: physical input type disagrees with semantic interface")
        if contract.has_default:
            if not hasattr(physical, "default_value"):
                raise CompileError("Internal error: semantic input default has no physical default")
            physical_default = canonicalize_group_input_default(contract.typ, physical.default_value)
            if physical_default != contract.default:
                raise CompileError("Internal error: physical input default disagrees with semantic interface")
    for physical, contract in zip(physical_outputs, prepared.interface.outputs):
        if getattr(physical, "name", "") != contract.display_name:
            raise CompileError("Internal error: physical output order/name disagrees with semantic interface")
        socket_type = getattr(physical, "socket_type", "") or getattr(physical, "bl_socket_idname", "")
        if socket_type != _socket_type_for(contract.typ):
            raise CompileError("Internal error: physical output type disagrees with semantic interface")


def _populate_group(
    group,
    request: BlenderGroupBuildRequest,
    *,
    generated_resource_transaction=None,
    group_backend=None,
):
    """Materialize one already-prepared group without parsing or semantic reanalysis."""
    if not isinstance(request, BlenderGroupBuildRequest):
        raise TypeError("request must be BlenderGroupBuildRequest")
    prepared = request.prepared_compilation
    identity = prepared.identity
    try:
        group.color_tag = "CONVERTER"
    except Exception:
        pass
    if identity.root_owner_id is not None:
        group[FUNCTION_ROOT_OWNER_ID_PROP] = identity.root_owner_id
    _store_group_source(group, prepared.source)
    try:
        group[INPUT_DEFAULTS_PROP] = {}
    except Exception:
        pass

    geometry_iface = None
    if prepared.geometry_mode:
        geometry_iface = group.interface.new_socket(
            name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
        )
    group_input = _new_node(group, "NodeGroupInput", -1100, 0)
    group_output = _new_node(group, "NodeGroupOutput", 1100, 0)
    group_output.is_active_output = True

    runtime_values = {}
    interface_items = {}
    for contract in prepared.interface.inputs:
        binding_id = contract.interface_origin
        if not isinstance(binding_id, BindingId):
            continue
        value, iface = _create_group_input_socket(
            group,
            group_input,
            contract.display_name,
            contract.typ,
            contract.default if contract.has_default else None,
        )
        runtime_values[binding_id] = value
        interface_items[binding_id] = iface

    geometry_value = None
    if prepared.geometry_mode:
        geometry_socket = next((socket for socket in group_input.outputs if socket.name == "Geometry"), None)
        if geometry_socket is None:
            raise CompileError("Internal error: prepared Geometry input did not materialize")
        geometry_value = make_value(geometry_socket, TYPE_GEOMETRY)

    session = request.source_callable_session
    materializer = FunctionMaterializer(group_backend=group_backend)
    materialization_context = FunctionMaterializationContext(
        function_group_cache=request.function_group_cache if request.function_group_cache is not None else {},
        function_group_transaction=request.function_group_transaction,
        function_compilation_trace=request.function_compilation_trace,
        source_callable_session=session,
    )
    lowering_context = BlenderIRLoweringContext(
        group=group,
        runtime_bindings=runtime_values,
        group_context_values=(
            {GroupContextSlot.CURRENT_GEOMETRY: geometry_value}
            if geometry_value is not None else {}
        ),
        interface_input_items=interface_items,
        source_callable_session=session,
        function_materializer=materializer,
        function_materialization_context=materialization_context,
        helper_namespace=request.helper_namespace or request.name,
        extension_registry=(
            session.resolved_environment.extension_registry
            if session is not None
            else None
        ),
        generated_resource_transaction=generated_resource_transaction,
    )

    trace_context = nullcontext(None)
    if request.function_compilation_inputs is not None and request.function_compilation_trace is not None:
        own_inputs = dict(request.function_compilation_inputs)
        own_inputs["lowered_source"] = prepared.normalized_lowered_source
        trace_context = request.function_compilation_trace.group(identity.declaration_owner, own_inputs)

    with trace_context as frame:
        if frame is not None:
            for owner_key, fingerprint in prepared.extension_dependencies:
                frame.record_dependency_identity(
                    make_function_group_owner_scope("EXTENSION", *owner_key),
                    fingerprint,
                )
        body_result = lower_body(
            lowering_context,
            prepared.body,
            runtime_values,
            base_depth=1,
            group_input=group_input,
        )

        if prepared.geometry_mode:
            current_geometry = body_result.group_context_values.get(GroupContextSlot.CURRENT_GEOMETRY)
            if not isinstance(current_geometry, Value) or current_geometry.typ is not TYPE_GEOMETRY:
                raise CompileError("Internal error: prepared geometry body lost current Geometry value")
            group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
            group.links.new(current_geometry.socket, group_output.inputs["Geometry"])

        body_outputs = (
            body_result.explicit_outputs
            if body_result.explicit_outputs
            else ((body_result.auto_output,) if body_result.auto_output is not None else ())
        )
        expected_outputs = prepared.interface.outputs[1:] if prepared.geometry_mode else prepared.interface.outputs
        if len(body_outputs) != len(expected_outputs):
            raise CompileError("Internal error: physical body output count disagrees with semantic summary")
        for (_source_name, value), contract in zip(body_outputs, expected_outputs):
            if value.typ is not contract.typ:
                raise CompileError("Internal error: physical output type disagrees with semantic output contract")
            group.interface.new_socket(
                name=contract.display_name,
                in_out="OUTPUT",
                socket_type=_socket_type_for(contract.typ),
            )
            group.links.new(value.socket, group_output.inputs[contract.display_name])

        _assert_prepared_interface_parity(group, prepared)
        if frame is not None:
            result = frame.finish(interface_contract(group))
            if not result.freshness_unproven:
                stamp_function_metadata(
                    group,
                    instance_key=request.function_instance_key,
                    definition_owner=identity.definition_owner,
                    fingerprint=result.fingerprint,
                )
    return group


def _new_group_backend(resolved_environment: ResolvedEnvironment | None = None):
    """Return a prepared-only physical backend bound to one environment snapshot."""
    slot = _ResolvedEnvironmentSlot(resolved_environment)

    def prepare(source, *, compilation_identity, source_callable_session=None, **kwargs):
        session = source_callable_session or SourceCallableSession(resolved_environment=slot.get())
        return analyze_group_source(
            source,
            compilation_identity=compilation_identity,
            resolved_environment=slot.get(),
            inherited_local_functions=kwargs.get("inherited_local_functions"),
            inherited_imported_library_functions=kwargs.get("inherited_imported_library_functions"),
            backend_builtins=kwargs.get("backend_builtins"),
            helper_namespace=kwargs.get("helper_namespace") or "NodeForge Group",
            source_callable_session=session,
        )

    return _ResolvedEnvironmentBoundBackend(
        populate_candidate=_populate_group,
        prepare_compilation=prepare,
        resolved_environment_for_session=slot.get,
    )


def _prepare_root_build(backend, source: str, *, name: str, existing_group=None):
    """Resolve final root identity and semantic compilation before Blender mutation."""
    session = backend.new_source_callable_session()
    identity = backend.resolve_root_compilation_identity(existing_group)
    prepared = backend.prepare_source_compilation(
        source,
        compilation_identity=identity,
        helper_namespace=name,
        source_callable_session=session,
    )
    return prepared, session


def create_expression_group(source: str, name: str = "NodeForge Group"):
    """Create a new Geometry Nodes group from one prepared NodeForge compilation."""
    backend = _new_group_backend()
    prepared, session = _prepare_root_build(backend, source, name=name)
    return backend.create_or_update(BlenderGroupBuildRequest(
        prepared_compilation=prepared,
        name=name,
        source_callable_session=session,
        helper_namespace=name,
    ))


def create_library_catalog_group(namespace: str, name: str):
    """Create or update a reusable node group for a catalog entry."""
    environment = resolve_environment()
    record = environment.catalog(namespace).find(name)
    if record is None:
        raise CompileError(f"Unknown {namespace} library entry: {name}")
    return materialize_library_entry_group_for_record(record, _new_group_backend(environment))


def create_library_function_group(name: str):
    """Create or update a reusable node group for a function-library entry."""
    return create_library_catalog_group("functions", name)


def update_library_catalog_group(group, namespace: str, name: str):
    """Reload a catalog-backed group through prepared semantic compilation."""
    environment = resolve_environment()
    record = environment.catalog(namespace).find(name)
    if record is None:
        raise CompileError(f"Current source for {namespace} library entry {name!r} is unavailable")
    return update_materialized_library_entry_group_for_record(
        record, group, _new_group_backend(environment)
    )


def update_expression_group(group, source: str):
    """Rebuild an existing Geometry Nodes group from one prepared compilation."""
    name = getattr(group, "name", "NodeForge Group")
    backend = _new_group_backend()
    prepared, session = _prepare_root_build(backend, source, name=name, existing_group=group)
    return backend.create_or_update(BlenderGroupBuildRequest(
        prepared_compilation=prepared,
        name=name,
        existing_group=group,
        source_callable_session=session,
        helper_namespace=name,
    ))


__all__ = [
    "CompileError",
    "Compiler",
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
