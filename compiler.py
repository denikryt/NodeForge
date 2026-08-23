"""Public compiler facade and high-level Geometry Nodes group assembly."""

import ast
from dataclasses import dataclass

import bpy

from .constants import TYPE_FLOAT, TYPE_INT, TYPE_TOKEN_NAMES, _ALLOWED_CONSTS
from .errors import CompileError
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
from .update import _apply_group_defaults_to_node, _capture_node_external_state, _restore_node_external_state
from .library import library_entry_names, materialize_library_entry_group, update_materialized_library_entry_group
from .statements import _unique_output_name
from .compile_time import reject_compile_time_object
from .systems import registry as systems_registry
from . import generated_resources
from . import expression_compiler
from .builtins import registry as builtin_registry

_TEST_CUTOVER_FAIL_AFTER_RESET = False
from .statement_compiler import GroupBuildContext, compile_statements


@dataclass(frozen=True)
class LibraryBinding:
    """Resolved source-local binding to one catalog entry."""

    namespace: str
    canonical_name: str


class LocalHelperBuildTransaction:
    """Own local-function helper side effects for one outer parent build attempt."""

    def __init__(self):
        self.created_groups = []
        self.updated_groups = []
        self._updated_by_name = {}
        self._closed = False

    def register_created(self, group, resource_transaction):
        """Track a newly created helper group and its generated-resource transaction."""
        if self._closed:
            return
        self.created_groups.append((group, resource_transaction))

    def register_updated(self, group, backup, resource_transaction, old_manifest, new_manifest):
        """Track a pre-existing helper update with rollback and deferred cleanup state."""
        if self._closed:
            _remove_node_group_if_live(backup)
            return
        key = getattr(group, "name", None)
        if key in self._updated_by_name:
            self._updated_by_name[key]["transactions"].append((resource_transaction, old_manifest, new_manifest))
            _remove_node_group_if_live(backup)
            return
        record = {
            "group": group,
            "backup": backup,
            "transactions": [(resource_transaction, old_manifest, new_manifest)],
        }
        self._updated_by_name[key] = record
        self.updated_groups.append(record)

    def rollback(self):
        """Restore all helper node groups and generated IDs touched by the attempt."""
        if self._closed:
            return
        self._closed = True
        for record in reversed(self.updated_groups):
            group = record["group"]
            backup = record["backup"]
            try:
                if group is not None and backup is not None and bpy.data.node_groups.get(group.name) is group:
                    _copy_group_contents(backup, group)
            except Exception:
                pass
            for tx, _old_manifest, _new_manifest in reversed(record["transactions"]):
                try:
                    tx.rollback()
                except Exception:
                    pass
            _remove_node_group_if_live(backup)
        for group, tx in reversed(self.created_groups):
            try:
                tx.rollback()
            except Exception:
                pass
            _remove_node_group_if_live(group)

    def commit(self):
        """Commit helper generated resources and perform deferred old-resource cleanup."""
        if self._closed:
            return
        self._closed = True
        for record in self.updated_groups:
            for tx, old_manifest, new_manifest in record["transactions"]:
                tx.mark_committed()
                if old_manifest is not None:
                    generated_resources.cleanup_previous_after_commit(
                        old_manifest,
                        new_manifest or generated_resources.build_manifest(old_manifest["owner_group_uuid"], []),
                    )
            _remove_node_group_if_live(record["backup"])
        for _group, tx in self.created_groups:
            tx.mark_committed()


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
        compile_group_callback=None,
        generated_resource_transaction=None,
        imported_library_functions=None,
        helper_namespace=None,
        local_helper_transaction=None,
        reserved_name_labels=None,
    ):
        """Initialize state shared by expression, statement, and call compilers."""
        self.group = group
        self.group_input = group_input
        self.vars = {}
        self.consts = consts if consts is not None else {}
        self.local_functions = local_functions or {}
        self.local_group_cache = local_group_cache if local_group_cache is not None else {}
        self.backend_builtins = dict(backend_builtins or {})
        self.compile_group_callback = compile_group_callback or _make_group
        self.generated_resource_transaction = generated_resource_transaction
        self.imported_library_functions = dict(imported_library_functions or {})
        self.helper_namespace = helper_namespace or getattr(group, "name", "Group")
        self.local_helper_transaction = local_helper_transaction
        self.reserved_name_labels = dict(reserved_name_labels or {})
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


def _validate_import_bindings(import_pairs, body_stmts, local_function_defs, backend_names, inherited_imports=None):
    """Validate and return source-local namespace-aware catalog bindings."""
    imported: dict[str, LibraryBinding] = {}
    local_bindings = _binding_names(body_stmts)
    reserved_names = set(builtin_registry.BUILTIN_NAMES) | {"output", "store", "panel"} | set(_ALLOWED_CONSTS) | set(systems_registry.constructor_names()) | set(backend_names) | set(TYPE_TOKEN_NAMES)

    def validate_pair(namespace, canonical_name, exposed_name, *, inherited=False):
        names = library_entry_names(namespace)
        if canonical_name not in names:
            raise CompileError(f"Unknown {namespace} import: {canonical_name}")
        binding = LibraryBinding(namespace, canonical_name)
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
            for library_name in sorted(library_entry_names(namespace), key=str.lower):
                validate_pair(namespace, library_name, library_name)
            continue
        validate_pair(namespace, import_request.canonical_name, import_request.exposed_name)
    for inherited_exposed, inherited_binding in dict(inherited_imports or {}).items():
        if isinstance(inherited_binding, LibraryBinding):
            validate_pair(inherited_binding.namespace, inherited_binding.canonical_name, inherited_exposed, inherited=True)
        else:
            validate_pair("functions", inherited_binding, inherited_exposed, inherited=True)
    return imported


def _registered_name_labels(local_function_defs, backend_names, imported_library_functions):
    """Return active DSL-owned names and human-readable reservation labels."""
    labels = {}

    def add(names, label):
        for name in names:
            labels.setdefault(name, label)

    add(builtin_registry.BUILTIN_NAMES, "DSL builtin")
    add({"output", "store", "panel"}, "reserved helper")
    add(_ALLOWED_CONSTS, "compile-time constant")
    add(systems_registry.constructor_names(), "embedded-system constructor")
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


def _build_group(
    source: str,
    name: str = "NodeForge Group",
    local_functions=None,
    backend_builtins=None,
    generated_resource_transaction=None,
    imported_library_functions=None,
    helper_namespace=None,
    local_helper_transaction=None,
):
    """Compile NodeForge source into a fresh GeometryNodeTree."""
    raw_stmts = _parse_source(source)
    raw_body_stmts, import_pairs = _extract_function_imports(raw_stmts)

    local_function_defs = dict(local_functions or {})
    for existing_local_name in local_function_defs:
        systems_registry.validate_no_reserved_collision(existing_local_name, "Local function")
    body_stmts = []
    for stmt in raw_body_stmts:
        if isinstance(stmt, ast.FunctionDef):
            systems_registry.validate_no_reserved_collision(stmt.name, "Local function")
            if stmt.name in local_function_defs:
                raise CompileError(f"Duplicate local function: {stmt.name}")
            local_function_defs[stmt.name] = stmt
        else:
            body_stmts.append(stmt)

    backend_names = set(backend_builtins or {})
    for helper_name in backend_names:
        systems_registry.validate_no_reserved_collision(helper_name, "Local backend helper")
    # Preserve the existing invariant that bundled catalog entries cannot use
    # reserved embedded-system names even when the current source has no imports.
    for namespace in ("functions", "examples"):
        library_entry_names(namespace)
    own_imported_library_functions = _validate_import_bindings(
        import_pairs,
        raw_body_stmts,
        local_function_defs,
        backend_names,
        inherited_imports=imported_library_functions,
    )
    reserved_name_labels = _registered_name_labels(
        local_function_defs,
        backend_names,
        own_imported_library_functions,
    )
    _validate_registered_name_bindings(
        raw_body_stmts,
        reserved_name_labels,
        top_level_function_names=local_function_defs,
    )
    _validate_interface_directive_placement(raw_body_stmts)

    stmts, consts = _preprocess_compile_time(body_stmts)
    callable_names = set(own_imported_library_functions) | set(local_function_defs) | backend_names | set(systems_registry.constructor_names())
    input_names = sorted(set(_collect_inputs(stmts, extra_builtin_names=callable_names, consts=consts)) - set(consts.keys()))
    input_types = _infer_input_types(stmts)
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    try:
        group.color_tag = 'CONVERTER'
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
            local_group_cache={},
            backend_builtins=backend_builtins,
            compile_group_callback=_make_group,
            generated_resource_transaction=generated_resource_transaction,
            imported_library_functions=own_imported_library_functions,
            helper_namespace=helper_namespace or group.name,
            local_helper_transaction=local_helper_transaction,
            reserved_name_labels=reserved_name_labels,
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
        compile_statements(ctx, stmts)

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

        return group
    except Exception:
        if local_helper_transaction is not None:
            local_helper_transaction.rollback()
        if generated_resource_transaction is not None:
            generated_resource_transaction.rollback()
        try:
            bpy.data.node_groups.remove(group, do_unlink=True)
        except Exception:
            pass
        raise

def _copy_custom_properties(src, dst, *, strict=False):
    """Replace destination custom properties with source custom properties.

    Group-level properties carry the stored source, input defaults, and generated-resource
    manifest.  During transactional cutover those properties are part of the committed
    contract, so failures must abort and roll back instead of being silently ignored.
    Node-level custom properties are best-effort because they are not authoritative
    NodeForge durable state.
    """
    def _handle(exc):
        if strict:
            raise exc

    try:
        keys = list(dst.keys())
    except Exception as exc:
        _handle(exc)
        keys = []
    for key in keys:
        try:
            del dst[key]
        except Exception as exc:
            _handle(exc)
    try:
        src_keys = list(src.keys())
    except Exception as exc:
        _handle(exc)
        src_keys = []
    for key in src_keys:
        try:
            dst[key] = src[key]
        except Exception as exc:
            _handle(exc)


def _copy_socket_default(src_socket, dst_socket):
    """Copy a socket default value when Blender exposes one."""
    if not hasattr(src_socket, "default_value") or not hasattr(dst_socket, "default_value"):
        return
    try:
        value = src_socket.default_value
        try:
            dst_socket.default_value = value
        except Exception:
            for index, component in enumerate(value):
                dst_socket.default_value[index] = component
    except Exception:
        pass


def _copy_node_properties(src_node, dst_node):
    """Copy writable RNA/custom properties needed by NodeForge-generated nodes."""
    skip = {"rna_type", "type", "dimensions", "inputs", "outputs", "internal_links", "select", "width_hidden", "height"}
    try:
        props = src_node.bl_rna.properties
    except Exception:
        props = []
    for prop in props:
        ident = getattr(prop, "identifier", "")
        if not ident or ident in skip or getattr(prop, "is_readonly", False):
            continue
        try:
            setattr(dst_node, ident, getattr(src_node, ident))
        except Exception:
            pass
    try:
        dst_node.location = tuple(src_node.location)
    except Exception:
        pass
    try:
        dst_node.name = src_node.name
    except Exception:
        pass
    try:
        dst_node.label = src_node.label
    except Exception:
        pass
    _copy_custom_properties(src_node, dst_node)
    for index, src_socket in enumerate(src_node.inputs):
        if index < len(dst_node.inputs):
            _copy_socket_default(src_socket, dst_node.inputs[index])
    for index, src_socket in enumerate(src_node.outputs):
        if index < len(dst_node.outputs):
            _copy_socket_default(src_socket, dst_node.outputs[index])


def _copy_interface(src_group, dst_group):
    """Copy sockets and native panel hierarchy between Geometry Node interfaces."""

    def item_pointer(item):
        try:
            return int(item.as_pointer())
        except Exception:
            return id(item)

    copied_items = {}
    child_positions = {}
    for item in getattr(src_group.interface, "items_tree", []):
        item_type = getattr(item, "item_type", None)
        src_parent = getattr(item, "parent", None)
        dst_parent = copied_items.get(item_pointer(src_parent)) if src_parent is not None else None
        parent_key = item_pointer(dst_parent) if dst_parent is not None else None
        position = child_positions.get(parent_key, 0)

        if item_type == "PANEL":
            copied = dst_group.interface.new_panel(
                name=item.name,
                description=getattr(item, "description", "") or "",
                default_closed=bool(getattr(item, "default_closed", False)),
            )
            if dst_parent is not None:
                dst_group.interface.move_to_parent(copied, dst_parent, position)
        elif item_type == "SOCKET":
            kwargs = {
                "name": item.name,
                "description": getattr(item, "description", "") or "",
                "in_out": item.in_out,
            }
            try:
                copied = dst_group.interface.new_socket(socket_type=item.socket_type, **kwargs)
            except Exception:
                copied = dst_group.interface.new_socket(
                    socket_type=getattr(item, "bl_socket_idname", "NodeSocketFloat"),
                    **kwargs,
                )
            if dst_parent is not None:
                dst_group.interface.move_to_parent(copied, dst_parent, position)
            _copy_socket_default(item, copied)
        else:
            continue

        copied_items[item_pointer(item)] = copied
        child_positions[parent_key] = position + 1


def _copy_group_contents(src_group, dst_group):
    """Replace dst_group graph/interface/properties with a copy of src_group."""
    _reset_node_group(dst_group)
    global _TEST_CUTOVER_FAIL_AFTER_RESET
    if _TEST_CUTOVER_FAIL_AFTER_RESET:
        _TEST_CUTOVER_FAIL_AFTER_RESET = False
        raise RuntimeError("Injected NodeForge cutover failure after destructive reset")
    _copy_interface(src_group, dst_group)
    from .builtins import raw_nodes as _raw_nodes

    node_map = {}
    for src_node in src_group.nodes:
        dst_node = dst_group.nodes.new(src_node.bl_idname)
        if _raw_nodes.is_raw_node(src_node):
            _raw_nodes.copy_raw_node_properties(src_node, dst_node)
        else:
            _copy_node_properties(src_node, dst_node)
        node_map[src_node.name] = dst_node
    _sync_repeat_zone_dynamic_items(src_group, node_map)
    _sync_capture_attribute_dynamic_items(src_group, node_map)

    for src_link in src_group.links:
        from_node = node_map.get(src_link.from_node.name)
        to_node = node_map.get(src_link.to_node.name)
        if from_node is None or to_node is None:
            continue
        if _raw_nodes.is_raw_node(src_link.from_node) or _raw_nodes.is_raw_node(src_link.to_node):
            from_socket = _raw_nodes.resolve_cutover_socket(src_link.from_node, src_link.from_socket, from_node, direction="output")
            to_socket = _raw_nodes.resolve_cutover_socket(src_link.to_node, src_link.to_socket, to_node, direction="input")
            dst_group.links.new(from_socket, to_socket)
            continue
        try:
            from_index = list(src_link.from_node.outputs).index(src_link.from_socket)
            to_index = list(src_link.to_node.inputs).index(src_link.to_socket)
            dst_group.links.new(from_node.outputs[from_index], to_node.inputs[to_index])
        except Exception:
            # Name fallback for dynamic sockets.
            try:
                dst_group.links.new(from_node.outputs[src_link.from_socket.name], to_node.inputs[src_link.to_socket.name])
            except Exception:
                raise
    for dst_node in node_map.values():
        if _raw_nodes.is_raw_node(dst_node):
            _raw_nodes.validate_raw_node_after_cutover(dst_node, dst_group)
    try:
        dst_group.color_tag = src_group.color_tag
    except Exception:
        pass
    try:
        dst_group.description = src_group.description
    except Exception:
        pass
    _copy_custom_properties(src_group, dst_group, strict=True)




def _sync_capture_attribute_dynamic_items(src_group, node_map):
    """Recreate Capture Attribute items before restoring copied links.

    Capture items define dynamic input/output sockets and are not copied by
    ordinary RNA property assignment. Rebuild them on the destination node so
    anonymous-attribute links survive transactional group cutover.
    """
    for src_node in src_group.nodes:
        if getattr(src_node, "bl_idname", None) != "GeometryNodeCaptureAttribute":
            continue
        dst_node = node_map.get(src_node.name)
        if dst_node is None or not hasattr(src_node, "capture_items") or not hasattr(dst_node, "capture_items"):
            continue
        try:
            for item in list(dst_node.capture_items):
                dst_node.capture_items.remove(item)
        except Exception:
            pass
        for item in list(src_node.capture_items):
            try:
                dst_node.capture_items.new(item.data_type, item.name)
            except Exception:
                # Link restoration below remains the transactional correctness
                # boundary if Blender cannot recreate a required socket.
                pass


def _sync_repeat_zone_dynamic_items(src_group, node_map):
    """Recreate Repeat Zone pairings/items after node copy and before links.

    Blender Repeat Zone state sockets are not ordinary writable node
    properties. A freshly created GeometryNodeRepeatInput/Output pair only has
    system/default sockets, so links to copied state sockets such as
    ``instance_points`` fail unless the output node's repeat_items collection is
    rebuilt before link restoration.
    """
    for src_input in src_group.nodes:
        if getattr(src_input, "bl_idname", None) != "GeometryNodeRepeatInput":
            continue
        src_output = getattr(src_input, "paired_output", None)
        if src_output is None:
            continue
        dst_input = node_map.get(src_input.name)
        dst_output = node_map.get(src_output.name)
        if dst_input is None or dst_output is None:
            continue
        try:
            dst_input.pair_with_output(dst_output)
        except Exception:
            pass
    for src_output in src_group.nodes:
        if getattr(src_output, "bl_idname", None) != "GeometryNodeRepeatOutput":
            continue
        dst_output = node_map.get(src_output.name)
        if dst_output is None or not hasattr(src_output, "repeat_items") or not hasattr(dst_output, "repeat_items"):
            continue
        try:
            for item in list(dst_output.repeat_items):
                dst_output.repeat_items.remove(item)
        except Exception:
            pass
        for item in list(src_output.repeat_items):
            try:
                dst_output.repeat_items.new(item.socket_type, item.name)
            except Exception:
                # Keep cutover failure transactional; link restoration below will
                # raise if the required socket was not recreated.
                pass
        try:
            dst_output.active_index = getattr(src_output, "active_index", dst_output.active_index)
        except Exception:
            pass
        try:
            dst_output.inspection_index = getattr(src_output, "inspection_index", dst_output.inspection_index)
        except Exception:
            pass

def _remove_node_group_if_live(group):
    try:
        if group is not None and bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)
    except Exception:
        pass


def _compile_fresh_with_cleanup(
    source,
    name,
    *,
    local_functions=None,
    backend_builtins=None,
    owner_group=None,
    imported_library_functions=None,
    helper_namespace=None,
    local_helper_transaction=None,
    defer_generated_resource_commit=False,
):
    """Compile a fresh group and clean temporary resources on failure."""
    if owner_group is not None:
        tx = generated_resources.create_transaction(owner_group)
    else:
        tx = generated_resources.GeneratedResourceTransaction(owner_group_uuid=__import__("uuid").uuid4().hex)
    group = None
    try:
        group = _build_group(
            source,
            name,
            local_functions=local_functions,
            backend_builtins=backend_builtins,
            generated_resource_transaction=tx,
            imported_library_functions=imported_library_functions,
            helper_namespace=helper_namespace,
            local_helper_transaction=local_helper_transaction,
        )
        if tx.resources:
            generated_resources.write_group_manifest(group, tx.manifest())
        if not defer_generated_resource_commit:
            tx.mark_committed()
        return group, tx
    except Exception:
        tx.rollback()
        _remove_node_group_if_live(group)
        raise


def _make_group(
    source: str,
    name: str = "NodeForge Group",
    existing_group=None,
    local_functions=None,
    backend_builtins=None,
    imported_library_functions=None,
    helper_namespace=None,
    local_helper_transaction=None,
):
    """Compile NodeForge source into a GeometryNodeTree."""
    if existing_group is None:
        top_level_helper_tx = local_helper_transaction or LocalHelperBuildTransaction()
        group, tx = _compile_fresh_with_cleanup(
            source,
            name,
            local_functions=local_functions,
            backend_builtins=backend_builtins,
            owner_group=None,
            imported_library_functions=imported_library_functions,
            helper_namespace=helper_namespace,
            local_helper_transaction=top_level_helper_tx,
            defer_generated_resource_commit=local_helper_transaction is not None,
        )
        if local_helper_transaction is not None:
            local_helper_transaction.register_created(group, tx)
        else:
            top_level_helper_tx.commit()
        return group
    if getattr(existing_group, "bl_idname", None) != "GeometryNodeTree":
        raise CompileError("Selected node group is not a GeometryNodeTree")
    return _update_existing_group_transactional(
        existing_group,
        source,
        name,
        local_functions=local_functions,
        backend_builtins=backend_builtins,
        imported_library_functions=imported_library_functions,
        helper_namespace=helper_namespace or getattr(existing_group, "name", name),
        local_helper_transaction=local_helper_transaction,
    )


def _update_existing_group_transactional(
    existing_group,
    source,
    name,
    *,
    local_functions=None,
    backend_builtins=None,
    imported_library_functions=None,
    helper_namespace=None,
    local_helper_transaction=None,
):
    """Compile into a replacement group, then cut over with rollback on cutover failure."""
    old_manifest = generated_resources.read_group_manifest(existing_group)
    owner_uuid = old_manifest["owner_group_uuid"] if old_manifest is not None else __import__("uuid").uuid4().hex
    replacement = None
    backup = None
    owns_helper_tx = local_helper_transaction is None
    build_helper_tx = local_helper_transaction or LocalHelperBuildTransaction()
    tx = generated_resources.GeneratedResourceTransaction(owner_group_uuid=owner_uuid)
    try:
        replacement = _build_group(
            source,
            "NodeForge.replacement." + name,
            local_functions=local_functions,
            backend_builtins=backend_builtins,
            generated_resource_transaction=tx,
            imported_library_functions=imported_library_functions,
            helper_namespace=helper_namespace or getattr(existing_group, "name", name),
            local_helper_transaction=build_helper_tx,
        )
        new_manifest = tx.manifest(empty=not bool(tx.resources)) if (old_manifest is not None or tx.resources) else None
        if new_manifest is not None:
            generated_resources.write_group_manifest(replacement, new_manifest)
        backup = existing_group.copy()
        backup.name = "NodeForge.rollback." + name
        try:
            _copy_group_contents(replacement, existing_group)
            if new_manifest is not None:
                generated_resources.write_group_manifest(existing_group, new_manifest)
            else:
                generated_resources.clear_group_manifest(existing_group)
        except Exception:
            try:
                _copy_group_contents(backup, existing_group)
            finally:
                raise
        if local_helper_transaction is not None:
            local_helper_transaction.register_updated(existing_group, backup, tx, old_manifest, new_manifest)
            backup = None
        else:
            build_helper_tx.commit()
            tx.mark_committed()
            if old_manifest is not None:
                generated_resources.cleanup_previous_after_commit(old_manifest, new_manifest or generated_resources.build_manifest(owner_uuid, []))
        return existing_group
    except Exception:
        if owns_helper_tx:
            build_helper_tx.rollback()
        tx.rollback()
        raise
    finally:
        _remove_node_group_if_live(replacement)
        _remove_node_group_if_live(backup)


def create_expression_group(source: str, name: str = "NodeForge Group"):
    """Create a new Geometry Nodes group from NodeForge source."""
    return _make_group(source, name)


def create_library_catalog_group(namespace: str, name: str):
    """Create or update a reusable node group for a catalog entry."""
    return materialize_library_entry_group(namespace, name, _make_group)


def create_library_function_group(name: str):
    """Create or update a reusable node group for a function-library entry."""
    return create_library_catalog_group("functions", name)


def update_library_catalog_group(group, namespace: str, name: str):
    """Reload a catalog-backed group in place from its current editable source."""
    return update_materialized_library_entry_group(namespace, name, group, _make_group)


def update_expression_group(group, source: str):
    """Rebuild an existing Geometry Nodes group from NodeForge source."""
    return _make_group(source, getattr(group, "name", "NodeForge Group"), existing_group=group)


__all__ = [
    "CompileError",
    "Compiler",
    "create_expression_group",
    "update_expression_group",
    "update_library_catalog_group",
    "create_library_catalog_group",
    "create_library_function_group",
    "_apply_group_defaults_to_node",
    "_capture_node_external_state",
    "_restore_node_external_state",
    "_extract_group_source",
    "_get_or_create_scratch_text",
    "_replace_text_contents",
]
