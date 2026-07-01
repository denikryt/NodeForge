"""Public compiler facade and high-level Geometry Nodes group assembly."""

import ast
from dataclasses import dataclass

import bpy

from .constants import TYPE_FLOAT, TYPE_INT, TYPE_TOKEN_NAMES, _ALLOWED_CONSTS
from .errors import CompileError
from .values import Value
from .nodes import (
    _new_node,
    _value,
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
from .library import library_entry_names, materialize_library_entry_group
from .statements import _unique_output_name
from .compile_time import reject_compile_time_object
from .systems import registry as systems_registry
from .systems.lsystem import resources as generated_resources
from . import expression_compiler
from .builtins import registry as builtin_registry

_TEST_CUTOVER_FAIL_AFTER_RESET = False
from .statement_compiler import GroupBuildContext, compile_statements


@dataclass(frozen=True)
class LibraryBinding:
    """Resolved source-local binding to one catalog entry."""

    namespace: str
    canonical_name: str


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
        self.depth = 0

    def compile(self, expr):
        """Compile one AST expression into this group's node tree."""
        self.depth += 1
        try:
            return expression_compiler.compile_expr(self, expr, self.depth)
        finally:
            self.depth -= 1

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
        val = Value(socket, typ)
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
    reserved_names = set(builtin_registry.BUILTIN_NAMES) | {"output", "store"} | set(_ALLOWED_CONSTS) | set(systems_registry.NAMES) | set(backend_names) | set(TYPE_TOKEN_NAMES)

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


def _build_group(source: str, name: str = "NodeForge Group", local_functions=None, backend_builtins=None, generated_resource_transaction=None, imported_library_functions=None):
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

    stmts, consts = _preprocess_compile_time(body_stmts)
    callable_names = set(own_imported_library_functions) | set(local_function_defs) | backend_names | systems_registry.NAMES
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
        )

        for socket in group_input.outputs:
            if socket.name in input_names:
                comp.vars[socket.name] = Value(socket, input_types.get(socket.name, TYPE_FLOAT))

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
    """Copy flat socket interface from one GeometryNodeTree to another."""
    for item in getattr(src_group.interface, "items_tree", []):
        if getattr(item, "item_type", None) != "SOCKET":
            continue
        try:
            sock = dst_group.interface.new_socket(
                name=item.name,
                in_out=item.in_out,
                socket_type=item.socket_type,
            )
        except Exception:
            sock = dst_group.interface.new_socket(
                name=item.name,
                in_out=item.in_out,
                socket_type=getattr(item, "bl_socket_idname", "NodeSocketFloat"),
            )
        _copy_socket_default(item, sock)


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


def _remove_node_group_if_live(group):
    try:
        if group is not None and bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)
    except Exception:
        pass


def _compile_fresh_with_cleanup(source, name, *, local_functions=None, backend_builtins=None, owner_group=None, imported_library_functions=None):
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
        )
        if tx.resources:
            generated_resources.write_group_manifest(group, tx.manifest())
        tx.mark_committed()
        return group, tx
    except Exception:
        tx.rollback()
        _remove_node_group_if_live(group)
        raise


def _make_group(source: str, name: str = "NodeForge Group", existing_group=None, local_functions=None, backend_builtins=None, imported_library_functions=None):
    """Compile NodeForge source into a GeometryNodeTree."""
    if existing_group is None:
        group, tx = _compile_fresh_with_cleanup(
            source,
            name,
            local_functions=local_functions,
            backend_builtins=backend_builtins,
            owner_group=None,
            imported_library_functions=imported_library_functions,
        )
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
    )


def _update_existing_group_transactional(existing_group, source, name, *, local_functions=None, backend_builtins=None, imported_library_functions=None):
    """Compile into a replacement group, then cut over with rollback on cutover failure."""
    old_manifest = generated_resources.read_group_manifest(existing_group)
    owner_uuid = old_manifest["owner_group_uuid"] if old_manifest is not None else __import__("uuid").uuid4().hex
    replacement = None
    backup = None
    tx = generated_resources.GeneratedResourceTransaction(owner_group_uuid=owner_uuid)
    try:
        replacement = _build_group(
            source,
            "NodeForge.replacement." + name,
            local_functions=local_functions,
            backend_builtins=backend_builtins,
            generated_resource_transaction=tx,
            imported_library_functions=imported_library_functions,
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
        tx.mark_committed()
        if old_manifest is not None:
            generated_resources.cleanup_previous_after_commit(old_manifest, new_manifest or generated_resources.build_manifest(owner_uuid, []))
        return existing_group
    except Exception:
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


def update_expression_group(group, source: str):
    """Rebuild an existing Geometry Nodes group from NodeForge source."""
    return _make_group(source, getattr(group, "name", "NodeForge Group"), existing_group=group)


__all__ = [
    "CompileError",
    "Compiler",
    "create_expression_group",
    "update_expression_group",
    "create_library_catalog_group",
    "create_library_function_group",
    "_apply_group_defaults_to_node",
    "_capture_node_external_state",
    "_restore_node_external_state",
    "_extract_group_source",
    "_get_or_create_scratch_text",
    "_replace_text_contents",
]
