"""Blender UI, operators, panels, and registration for NodeForge."""

import bpy
import traceback
from pathlib import Path
from bpy.types import Operator, Panel, PropertyGroup, UIList, AddonPreferences
from bpy.props import StringProperty, PointerProperty, CollectionProperty, IntProperty, BoolProperty

from .compiler import (
    compile_source_in_tree,
    create_selected_library_group_auto,
    update_mesh_compute_modifier,
    create_library_catalog_group,
    create_package_function_group,
    update_library_catalog_group,
)
from .blender.update import _apply_group_defaults_to_node
from .blender.storage import (
    _extract_group_source,
    _get_or_create_scratch_text,
    _replace_text_contents,
)
from .local_sources import (
    local_browser_records,
    local_source_roots,
    link_local_source_folder,
    unlink_local_source_folder,
    unlink_local_source_file,
    ensure_local_catalog_dir,
    create_local_folder,
    validate_new_local_name,
    delete_local_folder,
    save_local_source,
    delete_local_source,
)
from .blender.library_groups import apply_function_node_display_name
from .environment_resolution import resolve_environment, _make_compilation_environment
from . import packages
from .blender import generated_resources

def _source_from_props(props):
    """Return source code from the selected Blender Text datablock only."""
    if props and props.text_block:
        return props.text_block.as_string()
    return ""


def _active_gn_tree(context):
    """Function `_active_gn_tree` used by the NodeForge addon."""
    space = getattr(context, "space_data", None)
    if space and getattr(space, "type", None) == 'NODE_EDITOR':
        tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
        if tree and getattr(tree, "bl_idname", None) == "GeometryNodeTree":
            return tree
    return None

def _base_gn_tree(context):
    """Resolve only the base Geometry Nodes tree from the editor, never edit_tree."""
    space = getattr(context, "space_data", None)
    if (space is None or getattr(space, "type", None) != 'NODE_EDITOR'
            or getattr(space, 'tree_type', 'GeometryNodeTree') != 'GeometryNodeTree'):
        return None
    target = getattr(space, "node_tree", None)
    return target if getattr(target, "bl_idname", None) == 'GeometryNodeTree' else None



def _eligible_base_gn_tree(context):
    """Validate the currently opened editable base, excluding nested edit mode."""
    from .blender.root_update import validate_base_target
    from .errors import CompileError

    target = _base_gn_tree(context)
    space = getattr(context, 'space_data', None)
    if target is None:
        raise CompileError('Open a base Geometry Nodes tree in the Node Editor')
    edited = getattr(space, 'edit_tree', None)
    if edited is not None and edited.as_pointer() != target.as_pointer():
        raise CompileError('Return to the base Geometry Nodes tree before running Compile or Update')
    return validate_base_target(target)


def _selected_group_node(context):
    """Function `_selected_group_node` used by the NodeForge addon."""
    tree = _active_gn_tree(context)
    if tree is None:
        return None
    node = getattr(tree.nodes, "active", None)
    if node and getattr(node, "select", False) and getattr(node, "bl_idname", "") == "GeometryNodeGroup" and getattr(node, "node_tree", None):
        return node
    selected = [n for n in tree.nodes if getattr(n, "select", False) and getattr(n, "bl_idname", "") == "GeometryNodeGroup" and getattr(n, "node_tree", None)]
    if len(selected) == 1:
        return selected[0]
    return None



class NODEFORGE_OT_reload_addon(Operator):
    """Reload Blender scripts for NodeForge development."""
    bl_idname = "nodeforge.reload_addon"
    bl_label = "Reload NodeForge"
    bl_description = "Reload Blender scripts/addons from disk. Use this with a symlinked NodeForge folder."
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Use Blender's built-in script reload instead of manually unloading this addon."""
        try:
            bpy.ops.script.reload()
        except Exception as exc:
            self.report({'ERROR'}, f"Reload failed: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, "Reloaded Blender scripts")
        return {'FINISHED'}


class NODEFORGE_AddonPreferences(AddonPreferences):
    """Preferences shown under Edit > Preferences > Add-ons > NodeForge."""
    bl_idname = __package__ or "NodeForge"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Development")
        layout.operator(NODEFORGE_OT_reload_addon.bl_idname, text="Reload NodeForge", icon='FILE_REFRESH')
        layout.label(text="Use a symlinked addon folder for reliable development reloads.", icon='INFO')

class NODEFORGE_FunctionItem(PropertyGroup):
    """One row in a NodeForge catalog-library UI list."""
    name: StringProperty(name="Name", default="")
    kind: StringProperty(name="Kind", default="")
    path: StringProperty(name="Path", default="")
    folder_path: StringProperty(name="Folder", default="")
    package_id: StringProperty(name="Package ID", default="")
    package_name: StringProperty(name="Package", default="")
    root_path: StringProperty(name="Source Root", default="")
    source_label: StringProperty(name="Source", default="")
    member_path: StringProperty(name="Canonical Member", default="")
    materializable: BoolProperty(name="Source-backed Add", default=True)
    managed: BoolProperty(name="Managed", default=False)




class NODEFORGE_PackageItem(PropertyGroup):
    """One installed NodeForge package row shown in the UI."""

    id: StringProperty(name="ID", default="")
    package_id: StringProperty(name="Package ID", default="")
    name: StringProperty(name="Name", default="")
    version: StringProperty(name="Version", default="")
    origin: StringProperty(name="Origin", default="")
    status: StringProperty(name="Status", default="active")
    path: StringProperty(name="Path", default="")
    invalid_reason: StringProperty(name="Invalid Reason", default="")
    compatibility_warning: StringProperty(name="Compatibility Warning", default="")


class NODEFORGE_UL_function_library(UIList):
    """Draw available function-library entries in the N-panel."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        """Draw one catalog entry while keeping Local folders selectable."""
        row = layout.row(align=True)
        kind = getattr(item, "kind", "")
        if kind in {"folder", "linked_folder"}:
            row.label(text=item.name, icon='FILE_FOLDER')
            op = row.operator(
                "nodeforge.open_local_folder",
                text="",
                icon='FORWARD',
                emboss=False,
            )
            op.path = item.path
            return
        if kind in {"missing_linked_folder", "missing_linked_script"}:
            row.label(text=item.name, icon='ERROR')
            return
        if kind == "linked_script":
            row.label(text=item.name, icon='LINKED')
            return
        row.label(
            text=getattr(item, "source_label", "") or item.name,
            icon='CONSOLE' if kind in {'runtime', 'extension', 'native'} or item.path.endswith('.py') else 'NODETREE',
        )
        if getattr(item, "package_id", ""):
            row.label(text=item.package_id)




class NODEFORGE_UL_packages(UIList):
    """Draw installed NodeForge packages."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        """Draw one installed package or invalid package diagnostic."""
        row = layout.row(align=True)
        label = item.id or item.package_id or item.name
        if item.version:
            label = f"{label} {item.version}"
        icon_name = 'ERROR' if item.invalid_reason else 'INFO' if item.compatibility_warning else 'PACKAGE'
        row.label(text=label, icon=icon_name)
        if item.origin:
            row.label(text=item.origin)




def _refresh_package_items(props):
    """Reload active and invalid package records into the Scene collection."""
    old_index = getattr(props, "package_index", 0)
    old_id = ""
    if 0 <= old_index < len(props.package_items):
        old_id = props.package_items[old_index].package_id
    props.package_items.clear()
    for record in packages.active_package_records(include_invalid=True):
        item = props.package_items.add()
        if isinstance(record, packages.PackageDiagnostic):
            item.id = record.package_id
            item.package_id = record.package_id
            item.name = record.name or record.package_id
            item.version = record.version
            item.origin = record.origin
            item.status = "invalid"
            item.path = record.path
            item.compatibility_warning = ""
            item.invalid_reason = record.message
            continue
        manifest = record.manifest
        item.id = manifest.package_id
        item.package_id = manifest.package_id
        item.name = manifest.name
        item.version = manifest.version
        item.origin = str(record.state_record.get("origin") or manifest.origin)
        item.status = "installed"
        item.path = str(manifest.root)
        item.invalid_reason = ""
        item.compatibility_warning = packages.package_compatibility_warning(manifest)
    props.package_index = 0
    if old_id:
        for index, item in enumerate(props.package_items):
            if item.package_id == old_id:
                props.package_index = index
                break
    return len(props.package_items)


def _selected_package_item(props):
    """Return the selected installed package row, or None."""
    if props is None:
        return None
    index = getattr(props, "package_index", 0)
    if 0 <= index < len(props.package_items):
        return props.package_items[index]
    return None


def _items_for_namespace(props, namespace: str):
    """Return the UI collection/index pair for a library namespace."""
    if namespace == "functions":
        return props.function_items, "function_index"
    if namespace == "examples":
        return props.example_items, "example_index"
    if namespace == "local":
        return props.local_items, "local_index"
    raise ValueError(f"Unknown library namespace: {namespace}")


def _refresh_catalog_items(props, namespace: str):
    """Reload one catalog folder into its Scene collection property."""
    items, index_prop = _items_for_namespace(props, namespace)
    old_index = getattr(props, index_prop)
    old_name = ""
    old_path = ""
    old_kind = ""
    old_package_id = ""
    if 0 <= old_index < len(items):
        old_name = items[old_index].name
        old_path = getattr(items[old_index], "path", "")
        old_kind = getattr(items[old_index], "kind", "")
        old_package_id = getattr(items[old_index], "package_id", "")
    old_member_path = ""
    if 0 <= old_index < len(items):
        old_member_path = getattr(items[old_index], "member_path", "")
    items.clear()
    if namespace == "local":
        records = local_browser_records(getattr(props, "local_browser_path", ""))
    else:
        # The Examples UI does not resolve Local sources. Avoid eagerly walking
        # every Local root (including Python AST parsing) for an unrelated catalog.
        environment = (_make_compilation_environment() if namespace == "examples"
                       else resolve_environment())
        if namespace == "functions":
            # Library rows use the canonical per-definition index. A single .nf
            # module can export several named functions and different modules
            # may legitimately export the same leaf.
            records = []
            for package in sorted(environment.package_namespaces.values(),
                                  key=lambda owner: (owner.import_name.lower(), owner.package_id)):
                # A broken package retains its import reservation, while other
                # installed packages remain browsable in Library.
                if package.failure is not None:
                    continue
                for export in package.canonical_records():
                    if export.record is None:  # Systems are separate from Functions.
                        continue
                    record = export.record.as_dict()
                    record.update(name=export.name, member_path=export.qualified_name,
                                  source_label=export.qualified_name,
                                  materializable=export.definition is not None)
                    records.append(record)
        else:
            records = [record.as_dict() for record in environment.catalog(namespace).records()]
    from collections import Counter
    duplicate_names = Counter(record.get("name", "") for record in records) if namespace == "examples" else {}
    for record in records:
        item = items.add()
        item.name = record.get("name", "")
        item.kind = record.get("kind", "")
        item.path = record.get("path", "")
        item.folder_path = record.get("folder_path", "")
        if namespace == "functions":
            item.member_path = record["member_path"]
            item.materializable = record["materializable"]
        if hasattr(item, "package_id"):
            item.package_id = record.get("package_id", "")
            item.package_name = record.get("package_name", "")
        if hasattr(item, "root_path"):
            item.root_path = str(record.get("root_path", ""))
            # File rows show their own names; qualify duplicate Examples only.
            label = record.get("source_label", "") if namespace == "functions" else ""
            if namespace == "examples" and duplicate_names[record.get("name", "")] > 1:
                label = "/".join(part for part in (record.get("folder_path", ""), record.get("name", "")) if part)
            item.source_label = label
            item.managed = bool(record.get("managed", False))
    setattr(props, index_prop, 0)
    if old_name:
        for index, item in enumerate(items):
            if namespace == "local":
                if item.name == old_name and item.path == old_path and item.kind == old_kind:
                    setattr(props, index_prop, index)
                    break
            elif namespace == "functions":
                if (getattr(item, "package_id", "") == old_package_id
                        and item.member_path == old_member_path):
                    setattr(props, index_prop, index)
                    break
            elif (item.name == old_name and item.path == old_path
                  and getattr(item, "package_id", "") == old_package_id):
                setattr(props, index_prop, index)
                break
    return len(records)


def _selected_catalog_item(props, namespace: str):
    """Return the selected catalog-library item, or None."""
    if props is None:
        return None
    items, index_prop = _items_for_namespace(props, namespace)
    index = getattr(props, index_prop)
    if 0 <= index < len(items):
        return items[index]
    return None


class GNSCRIPT_MVP_Properties(PropertyGroup):
    """Properties stored on the current Scene for NodeForge UI state."""
    text_block: PointerProperty(
        name="Text Script",
        description="Blender Text datablock used as the NodeForge source script.",
        type=bpy.types.Text,
    )
    function_items: CollectionProperty(type=NODEFORGE_FunctionItem)
    function_index: IntProperty(name="Function", default=0)
    example_items: CollectionProperty(type=NODEFORGE_FunctionItem)
    example_index: IntProperty(name="Example", default=0)
    local_items: CollectionProperty(type=NODEFORGE_FunctionItem)
    local_index: IntProperty(name="Local", default=0)
    package_items: CollectionProperty(type=NODEFORGE_PackageItem)
    package_index: IntProperty(name="Package", default=0)
    package_archive_path: StringProperty(name="Package Archive", default="", subtype="FILE_PATH")
    local_browser_path: StringProperty(name="Local Browser Path", default="", options={'HIDDEN'})
    local_script_name: StringProperty(name="Script Name", default="")
    local_overwrite: BoolProperty(name="Overwrite", default=False)

class GNSCRIPT_MVP_OT_compile_expression(Operator):
    """Compile a selected Text into the current base using one semantic route."""
    bl_idname = "gn_script_mvp.compile_expression"
    bl_label = "Compile Script"
    bl_description = "Replace the root graph or update function groups from the selected script"
    bl_options = {'REGISTER', 'UNDO'}

    expression: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        """Provide a precise target diagnostic during execute."""
        return getattr(context, 'scene', None) is not None

    def execute(self, context):
        """Use one Compile route, retaining Undo when cleanup may be incomplete."""
        from .compiler import ensure_source_text_context
        from .compiler_identities import TEXT_MODULE_CONTEXT_PROP
        from .blender.root_update import RootUpdateError

        text = None
        context_added = False
        module_context = None
        try:
            target = _eligible_base_gn_tree(context)
            props = getattr(context.scene, 'gn_script_mvp', None)
            text = None if self.expression else getattr(props, 'text_block', None)
            source = self.expression or _source_from_props(props)
            if not isinstance(source, str) or not source.strip():
                raise ValueError('Compile Script requires a nonblank selected Text source')
            if text is not None:
                context_added = TEXT_MODULE_CONTEXT_PROP not in text
                module_context = ensure_source_text_context(text)
            result = compile_source_in_tree(source, target, module_context=module_context)
        except Exception as exc:
            traceback.print_exc()
            might_have_changed = isinstance(exc, RootUpdateError) and exc.may_have_mutated
            message = str(exc)
            if not might_have_changed and context_added and text is not None:
                try:
                    if module_context is None or text.get(TEXT_MODULE_CONTEXT_PROP) == module_context:
                        del text[TEXT_MODULE_CONTEXT_PROP]
                except Exception as cleanup_error:
                    might_have_changed = True
                    message += f'; Text identity cleanup: {cleanup_error}'
            self.report({'ERROR'}, message)
            return {'FINISHED'} if might_have_changed else {'CANCELLED'}

        if result.skipped_boundary_links:
            self.report({'WARNING'},
                        f'Compiled {target.name}; skipped {result.skipped_boundary_links} incompatible or ambiguous boundary link(s)')
        elif result.outcome == 'GROUPS_ONLY':
            self.report({'INFO'}, f'Updated function groups from: {target.name}')
        else:
            self.report({'INFO'}, f'Compiled root graph in: {target.name}')
        return {'FINISHED'}


def _report_mesh_compute_evaluation(operator, context, group):
    """Evaluate a committed runtime modifier and report nonfatal callback failure."""
    from .blender.mesh_compute_runtime import evaluate_modifier_binding

    try:
        evaluate_modifier_binding(context.active_object)
    except Exception as exc:
        operator.report({'WARNING'}, f"Compiled {group.name}; runtime error: {exc}")
    else:
        operator.report({'INFO'}, f"Mesh Compute modifier ready: {group.name}")


class NODEFORGE_OT_install_mesh_compute_modifier(Operator):
    """Explicitly install a typed .nf Mesh Compute function as a private modifier."""

    bl_idname = "nodeforge.install_mesh_compute_modifier"
    bl_label = "Install Mesh Compute"
    bl_description = "Compile the Text block's typed Mesh Compute entry into a private modifier"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        owner = getattr(context, "active_object", None)
        props = getattr(getattr(context, "scene", None), "gn_script_mvp", None)
        return bool(owner is not None and owner.type == "MESH"
                    and props is not None and props.text_block is not None)

    def execute(self, context):
        from .compiler import create_mesh_compute_modifier

        try:
            group = create_mesh_compute_modifier(
                _source_from_props(context.scene.gn_script_mvp), context.active_object,
            )
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _report_mesh_compute_evaluation(self, context, group)
        return {'FINISHED'}


class NODEFORGE_OT_reload_mesh_compute_modifier(Operator):
    """Recompile the current runtime root with atomic socket/FCurve cutover."""

    bl_idname = "nodeforge.reload_mesh_compute_modifier"
    bl_label = "Reload Compute"
    bl_description = "Reload Local Mesh Compute code while preserving modifier inputs and animation"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Offer Reload only when the exact active Object has a runtime binding."""
        from .blender.mesh_compute_runtime import get_modifier_binding, saved_runtime_status

        owner = getattr(context, "active_object", None)
        props = getattr(getattr(context, "scene", None), "gn_script_mvp", None)
        if owner is None or owner.type != 'MESH' or props is None or props.text_block is None:
            return False
        if saved_runtime_status(owner) == 'MISSING_RUNTIME':
            return True
        try:
            return get_modifier_binding(owner) is not None
        except (ValueError, ReferenceError):
            return False

    def execute(self, context):
        """Attempt one committed runtime generation without accepting partial Reload."""
        from .blender.mesh_compute_runtime import evaluate_modifier_binding

        owner = context.active_object
        try:
            group = update_mesh_compute_modifier(_source_from_props(context.scene.gn_script_mvp), owner)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        try:
            evaluate_modifier_binding(owner)
        except Exception as exc:
            self.report({'WARNING'}, f"Reloaded {group.name}; runtime error: {exc}")
            return {'FINISHED'}
        self.report({'INFO'}, f"Reloaded Mesh Compute: {group.name}")
        return {'FINISHED'}


class NODEFORGE_OT_detach_selected_source_owner(Operator):
    """Explicitly remove authoritative source stamps from a selected user copy."""
    bl_idname = "nodeforge.detach_selected_source_owner"
    bl_label = "Detach Source Ownership"
    bl_description = "Detach the selected single-user copy from canonical NodeForge source updates"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Only a selected Group Node can nominate the exact copy to detach."""
        return _selected_group_node(context) is not None

    def execute(self, context):
        """Fail closed if the selected datablock is not an eligible user copy."""
        from .blender.group_authority import detach_selected_source_owner

        node = _selected_group_node(context)
        if node is None:
            self.report({'ERROR'}, "Select one Geometry Node Group copy")
            return {'CANCELLED'}
        try:
            detach_selected_source_owner(node.node_tree)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Detached source ownership: {node.node_tree.name}")
        return {'FINISHED'}


class NODEFORGE_OT_reload_selected_library_group(Operator):
    """Reload a selected source-backed Function Node Group in place."""
    bl_idname = "nodeforge.reload_selected_library_group"
    bl_label = "Reload from Source"
    bl_description = "Rebuild the selected NodeForge function from its current source while retaining the group datablock"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Enable for a selected group that carries NodeForge library provenance.

        Keep catalog/source resolution out of this UI hot path. Blender may call
        operator polls repeatedly while redrawing the N-panel, so filesystem and
        package validation belongs in :meth:`execute` after the user clicks Reload.
        """
        node = _selected_group_node(context)
        if node is None:
            return False
        group = node.node_tree
        try:
            if str(group.get('nodeforge_module_definition') or ''):
                return True
            namespace = str(group.get("nodeforge_library_namespace") or "")
            name = str(group.get("nodeforge_library_name") or "")
        except Exception:
            return False
        return bool(namespace and name)

    def execute(self, context):
        """Reload the selected group in place and restore its external node state."""
        node = _selected_group_node(context)
        if node is None:
            self.report({'ERROR'}, "Select exactly one reloadable NodeForge library group")
            return {'CANCELLED'}
        old_name = node.node_tree.name
        try:
            update_library_catalog_group(node.node_tree)
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        node.node_tree.name = old_name
        node.name = node.name or old_name
        self.report({'INFO'}, f"Reloaded node group: {old_name}")
        return {'FINISHED'}


class GNSCRIPT_MVP_OT_load_selected_group_source(Operator):
    """Class `GNSCRIPT_MVP_OT_load_selected_group_source` used by the NodeForge addon."""
    bl_idname = "gn_script_mvp.load_selected_group_source"
    bl_label = "Load Script From Selected NodeGroup"
    bl_description = "Load the embedded NodeForge source from the selected Geometry Node Group into the selected Text datablock"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Function `poll` used by the NodeForge addon."""
        node = _selected_group_node(context)
        return node is not None and bool(_extract_group_source(node.node_tree))

    def execute(self, context):
        """Function `execute` used by the NodeForge addon."""
        props = getattr(context.scene, "gn_script_mvp", None)
        node = _selected_group_node(context)
        if node is None:
            self.report({'ERROR'}, "Select exactly one Geometry Node Group node")
            return {'CANCELLED'}
        source = _extract_group_source(node.node_tree)
        if not source:
            self.report({'ERROR'}, "Selected node group has no embedded NodeForge source")
            return {'CANCELLED'}
        if props is None:
            self.report({'ERROR'}, "NodeForge properties are not available")
            return {'CANCELLED'}
        text = props.text_block or _get_or_create_scratch_text()
        _replace_text_contents(text, source)
        props.text_block = text
        self.report({'INFO'}, f"Loaded script from: {node.node_tree.name}")
        return {'FINISHED'}


class NODEFORGE_OT_refresh_function_library(Operator):
    """Refresh one namespace in the NodeForge library UI."""
    bl_idname = "nodeforge.refresh_function_library"
    bl_label = "Refresh Library"
    bl_description = "Reload available catalog entries"
    bl_options = {'REGISTER'}

    namespace: StringProperty(default="functions")

    def execute(self, context):
        """Reload catalog entries into the N-panel list."""
        props = getattr(context.scene, "gn_script_mvp", None)
        if props is None:
            self.report({'ERROR'}, "NodeForge properties are not available")
            return {'CANCELLED'}
        try:
            count = _refresh_catalog_items(props, self.namespace)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Found {count} {self.namespace} item(s)")
        return {'FINISHED'}




class NODEFORGE_OT_refresh_packages(Operator):
    """Refresh the installed NodeForge package list."""

    bl_idname = "nodeforge.refresh_packages"
    bl_label = "Refresh Packages"
    bl_description = "Reload installed NodeForge package records"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Reload package rows into the N-panel list."""
        props = getattr(context.scene, "gn_script_mvp", None)
        if props is None:
            self.report({'ERROR'}, "NodeForge properties are not available")
            return {'CANCELLED'}
        try:
            count = _refresh_package_items(props)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Found {count} package item(s)")
        return {'FINISHED'}


class NODEFORGE_OT_import_package(Operator):
    """Install or replace a NodeForge package archive."""

    bl_idname = "nodeforge.import_package"
    bl_label = "Import NodeForge Package"
    bl_description = "Install a NodeForge package zip archive"
    bl_options = {'REGISTER'}

    filepath: StringProperty(name="Package Archive", subtype="FILE_PATH", default="")
    replace: BoolProperty(name="Replace existing package", default=False)

    def invoke(self, context, event):
        """Open Blender's file selector for zip package import."""
        props = getattr(context.scene, "gn_script_mvp", None)
        if props is not None:
            self.filepath = props.package_archive_path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        """Install the selected package archive and refresh package/catalog UI."""
        props = getattr(context.scene, "gn_script_mvp", None)
        try:
            manifest = packages.install_package_zip(self.filepath, replace=self.replace)
            if props is not None:
                props.package_archive_path = self.filepath
                _refresh_package_items(props)
                _refresh_catalog_items(props, "functions")
                _refresh_catalog_items(props, "examples")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Installed package: {manifest.package_id}")
        return {'FINISHED'}


class NODEFORGE_OT_uninstall_package(Operator):
    """Uninstall the selected NodeForge package."""

    bl_idname = "nodeforge.uninstall_package"
    bl_label = "Uninstall Package"
    bl_description = "Remove the selected package from future NodeForge library resolution"
    bl_options = {'REGISTER'}

    package_id: StringProperty(name="Package ID", default="")

    def execute(self, context):
        """Remove active package state and refresh package/catalog UI."""
        props = getattr(context.scene, "gn_script_mvp", None)
        item = _selected_package_item(props)
        package_id = self.package_id or (item.package_id if item is not None else "")
        if not package_id:
            self.report({'ERROR'}, "Select a package to uninstall")
            return {'CANCELLED'}
        try:
            packages.uninstall_package(package_id)
            if props is not None:
                _refresh_package_items(props)
                _refresh_catalog_items(props, "functions")
                _refresh_catalog_items(props, "examples")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Uninstalled package: {package_id}")
        return {'FINISHED'}


class NODEFORGE_OT_create_function_group(Operator):
    """Insert the selected catalog entry into the active Geometry Nodes editor."""
    bl_idname = "nodeforge.create_function_group"
    bl_label = "Add Library Node Group"
    bl_description = "Add the selected Node Group or install a selected Mesh Compute root modifier"
    bl_options = {'REGISTER', 'UNDO'}

    namespace: StringProperty(default="functions")

    @classmethod
    def poll(cls, context):
        """Enable whenever NodeForge scene properties exist."""
        return getattr(context.scene, "gn_script_mvp", None) is not None

    def execute(self, context):
        """Materialize the selected catalog entry and insert it as a group node."""
        props = getattr(context.scene, "gn_script_mvp", None)
        item = _selected_catalog_item(props, self.namespace)
        if item is None:
            self.report({'ERROR'}, f"Select a {self.namespace} entry from the list")
            return {'CANCELLED'}
        if self.namespace == "examples" and getattr(item, "kind", "") == "runtime":
            self.report({'ERROR'}, "Executable Examples Python can only run inside a root modifier")
            return {'CANCELLED'}
        if self.namespace == "local" and getattr(item, "kind", "") not in {"script", "linked_script"}:
            self.report({'ERROR'}, "Select a Local script, not a folder")
            return {'CANCELLED'}
        if self.namespace == "functions" and not getattr(item, "materializable", False):
            self.report({'ERROR'},
                "Native Extension API v2 callables are used in .nf scripts; "
                "Library Add requires a source-backed .nf definition")
            return {'CANCELLED'}

        tree = _active_gn_tree(context)
        if tree is None and self.namespace not in {"local", "examples"}:
            self.report({'ERROR'}, "Open a Geometry Nodes editor to add a library node group")
            return {'CANCELLED'}

        try:
            if self.namespace == "functions":
                group = create_package_function_group(item.package_id, item.member_path)
                installed_modifier = False
            elif self.namespace in {"local", "examples"}:
                group, installed_modifier = create_selected_library_group_auto(
                    self.namespace, item.path, context.active_object, item.package_id,
                    editor_available=tree is not None,
                )
            else:
                group = create_library_catalog_group(self.namespace, item.name)
                installed_modifier = False
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        if installed_modifier:
            _report_mesh_compute_evaluation(self, context, group)
            return {'FINISHED'}

        node = tree.nodes.new("GeometryNodeGroup")
        node.node_tree = group
        apply_function_node_display_name(node, group)
        _apply_group_defaults_to_node(node)
        node.location = (0, 0)
        tree.nodes.active = node
        for other in tree.nodes:
            other.select = False
        node.select = True
        self.report({'INFO'}, f"Added library node group: {group.name}")
        return {'FINISHED'}


def _managed_local_browser_directory(props) -> Path | None:
    """Return the current managed Local directory, or None inside an external source."""
    root = ensure_local_catalog_dir().resolve()
    raw = getattr(props, "local_browser_path", "") if props is not None else ""
    if not raw:
        return root
    current = Path(raw).expanduser().resolve(strict=False)
    try:
        current.relative_to(root)
    except ValueError:
        return None
    return current if current.is_dir() else root


def _local_browser_source_root(path: Path) -> Path | None:
    """Return the configured Local source root containing *path*."""
    resolved = path.expanduser().resolve(strict=False)
    for record in local_source_roots(include_missing=True):
        root = Path(record["path"]).resolve(strict=False)
        try:
            resolved.relative_to(root)
            return root
        except ValueError:
            continue
    return None


class NODEFORGE_OT_open_local_folder(Operator):
    """Navigate the Local mini file browser into one visible folder."""

    bl_idname = "nodeforge.open_local_folder"
    bl_label = "Open Local Folder"
    bl_options = {'INTERNAL'}

    path: StringProperty(name="Folder", subtype="DIR_PATH", default="")

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        target = Path(self.path).expanduser().resolve(strict=False)
        if props is None or not target.is_dir() or _local_browser_source_root(target) is None:
            self.report({'ERROR'}, "Local folder is unavailable")
            return {'CANCELLED'}
        props.local_browser_path = str(target)
        _refresh_catalog_items(props, "local")
        return {'FINISHED'}


class NODEFORGE_OT_local_browser_back(Operator):
    """Navigate the Local mini file browser to its parent directory."""

    bl_idname = "nodeforge.local_browser_back"
    bl_label = "Back"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        props = getattr(getattr(context, "scene", None), "gn_script_mvp", None)
        return props is not None and bool(getattr(props, "local_browser_path", ""))

    def execute(self, context):
        props = context.scene.gn_script_mvp
        current = Path(props.local_browser_path).expanduser().resolve(strict=False)
        root = _local_browser_source_root(current)
        if root is None or current == root:
            props.local_browser_path = ""
        else:
            parent = current.parent
            props.local_browser_path = "" if parent == ensure_local_catalog_dir().resolve() else str(parent)
        _refresh_catalog_items(props, "local")
        return {'FINISHED'}


class NODEFORGE_OT_import_local(Operator):
    """Add one external directory as a read-only Local source root."""

    bl_idname = "nodeforge.import_local"
    bl_label = "Add Folder"
    bl_description = "Add an external folder to Local without copying or modifying its contents"
    bl_options = {'REGISTER'}

    directory: StringProperty(subtype="DIR_PATH", default="")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        folder = Path(self.directory).expanduser()
        try:
            if not folder.is_dir():
                raise ValueError("Select an existing folder")
            linked = link_local_source_folder(str(folder))
            if props is not None:
                props.local_browser_path = ""
                _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Added Local folder: {linked.name}")
        return {'FINISHED'}


class NODEFORGE_OT_delete_local_file(Operator):
    """Delete the concrete managed Local source selected in the browser."""

    bl_idname = "nodeforge.delete_local_file"
    bl_label = "Delete File"
    bl_description = "Delete the selected file from NodeForge-managed Local storage"
    bl_options = {'REGISTER'}

    path: StringProperty(name="Path", default="", options={'HIDDEN'})

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        try:
            deleted = delete_local_source(self.path)
            if props is not None:
                _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Deleted Local file: {deleted.name}")
        return {'FINISHED'}


class NODEFORGE_OT_delete_local_folder(Operator):
    """Delete the concrete empty managed Local folder selected in the browser."""

    bl_idname = "nodeforge.delete_local_folder"
    bl_label = "Delete Folder"
    bl_description = "Delete the selected empty folder from NodeForge-managed Local storage"
    bl_options = {'REGISTER'}

    path: StringProperty(name="Path", default="", options={'HIDDEN'})

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        try:
            deleted = delete_local_folder(self.path)
            if props is not None:
                _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Deleted Local folder: {deleted.name}")
        return {'FINISHED'}


class NODEFORGE_OT_remove_local_source(Operator):
    """Remove one imported Local root or legacy linked file without touching disk."""

    bl_idname = "nodeforge.remove_local_source"
    bl_label = "Remove from Local"
    bl_description = "Remove the selected external source registration without deleting external files"
    bl_options = {'REGISTER'}

    path: StringProperty(name="Path", default="", options={'HIDDEN'})
    kind: StringProperty(name="Kind", default="folder", options={'HIDDEN'})

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        try:
            target = Path(self.path).expanduser().resolve(strict=False)
            if self.kind == "file":
                removed = unlink_local_source_file(target)
            else:
                removed = unlink_local_source_folder(target)
            if props is not None:
                current_raw = getattr(props, "local_browser_path", "")
                if current_raw:
                    current = Path(current_raw).expanduser().resolve(strict=False)
                    try:
                        current.relative_to(target)
                    except ValueError:
                        pass
                    else:
                        props.local_browser_path = ""
                _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Removed from Local: {removed.name}")
        return {'FINISHED'}


class NODEFORGE_OT_create_local_folder(Operator):
    """Create one folder inside the current managed Local directory."""

    bl_idname = "nodeforge.create_local_folder"
    bl_label = "New Folder"
    bl_description = "Create a folder in the current Local directory"
    bl_options = {'REGISTER'}

    folder_name: StringProperty(name="Name", default="")

    @classmethod
    def poll(cls, context):
        props = getattr(getattr(context, "scene", None), "gn_script_mvp", None)
        return _managed_local_browser_directory(props) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.gn_script_mvp
        current = _managed_local_browser_directory(props)
        if current is None:
            self.report({'ERROR'}, "External Local folders are read-only")
            return {'CANCELLED'}
        try:
            validate_new_local_name(self.folder_name, directory=True)
            root = ensure_local_catalog_dir().resolve()
            rel_parent = current.relative_to(root)
            folder_path = str(rel_parent / self.folder_name) if rel_parent.parts else self.folder_name
            created = create_local_folder(folder_path)
            _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Created folder: {created.name}")
        return {'FINISHED'}


class NODEFORGE_OT_save_to_local(Operator):
    """Save the selected Blender Text script into the current managed Local directory."""

    bl_idname = "nodeforge.save_to_local"
    bl_label = "Save"
    bl_description = "Save the selected Blender Text script in the current Local directory"
    bl_options = {'REGISTER'}

    script_name: StringProperty(name="Name", default="")
    overwrite: BoolProperty(name="Overwrite", default=False)

    @classmethod
    def poll(cls, context):
        props = getattr(getattr(context, "scene", None), "gn_script_mvp", None)
        return props is not None and props.text_block is not None and _managed_local_browser_directory(props) is not None

    def invoke(self, context, event):
        props = context.scene.gn_script_mvp
        if not self.script_name and props.text_block is not None:
            text_name = Path(props.text_block.name).name
            self.script_name = Path(text_name).stem if text_name.lower().endswith(".nf") else text_name
        self.overwrite = bool(getattr(props, "local_overwrite", False))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = getattr(getattr(context, "scene", None), "gn_script_mvp", None)
        if props is None or props.text_block is None:
            self.report({'ERROR'}, "Select a Text Block to save")
            return {'CANCELLED'}
        current = _managed_local_browser_directory(props)
        if current is None:
            self.report({'ERROR'}, "External Local folders are read-only")
            return {'CANCELLED'}
        root = ensure_local_catalog_dir().resolve()
        try:
            source = props.text_block.as_string()
            if not source.strip():
                self.report({'ERROR'}, "The selected Text Block is empty")
                return {'CANCELLED'}
            rel = current.relative_to(root)
            folder_path = str(rel) if rel.parts else ""
            path = save_local_source(self.script_name, source, folder_path=folder_path, overwrite=self.overwrite)
            props.local_script_name = self.script_name
            props.local_overwrite = self.overwrite
            _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved Local script: {path.name}")
        return {'FINISHED'}


def _draw_local_selected_action(layout, item) -> None:
    """Draw the ownership-specific destructive action for one selected Local row."""
    if item is None:
        return
    kind = getattr(item, "kind", "")
    managed = bool(getattr(item, "managed", False))
    path = getattr(item, "path", "")
    if kind == "script" and managed:
        op = layout.operator(NODEFORGE_OT_delete_local_file.bl_idname, text="Delete File", icon='TRASH')
        op.path = path
    elif kind == "folder" and managed:
        op = layout.operator(NODEFORGE_OT_delete_local_folder.bl_idname, text="Delete Folder", icon='TRASH')
        op.path = path
    elif kind in {"linked_folder", "missing_linked_folder"}:
        op = layout.operator(NODEFORGE_OT_remove_local_source.bl_idname, text="Remove from Local", icon='X')
        op.path = path
        op.kind = "folder"
    elif kind in {"linked_script", "missing_linked_script"}:
        op = layout.operator(NODEFORGE_OT_remove_local_source.bl_idname, text="Remove from Local", icon='X')
        op.path = path
        op.kind = "file"


def _draw_library_catalog_panel(layout, context, namespace: str, collection_name: str, index_name: str, rows: int):
    """Draw one collapsible library catalog panel body."""
    props = context.scene.gn_script_mvp
    row = layout.row(align=True)
    op = row.operator(NODEFORGE_OT_refresh_function_library.bl_idname, text="Refresh", icon='FILE_REFRESH')
    op.namespace = namespace
    items = getattr(props, collection_name)
    if not items:
        layout.label(text="Click Refresh to scan this catalog", icon='INFO')
    layout.template_list(
        NODEFORGE_UL_function_library.__name__,
        namespace,
        props,
        collection_name,
        props,
        index_name,
        rows=rows,
    )
    item = _selected_catalog_item(props, namespace)
    row = layout.row(align=True)
    editor_ready = _active_gn_tree(context) is not None
    selected_script = (item is not None and namespace in {"local", "examples"}
                       and item.kind in {"script", "linked_script"}
                       and item.path.endswith(".nf"))
    row.enabled = (item is not None and (editor_ready or selected_script)
                   and not (namespace in {"local", "examples"} and
                            (item.kind == "runtime" or item.path.endswith(".py")))
                   and not (namespace == "functions" and not getattr(item, "materializable", False)))
    if namespace == "functions" and item is not None and not item.materializable:
        layout.label(text="Native callable: use it from an .nf script", icon='INFO')
    op = row.operator(NODEFORGE_OT_create_function_group.bl_idname, text="Add Node Group", icon='NODETREE')
    op.namespace = namespace
    if namespace == "local":
        _draw_local_selected_action(layout, item)
    if not editor_ready:
        layout.label(text="Ordinary groups require an open Geometry Nodes editor", icon='INFO')


class GNSCRIPT_MVP_PT_panel(Panel):
    """Class `GNSCRIPT_MVP_PT_panel` used by the NodeForge addon."""
    bl_label = "NodeForge"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "NodeForge"

    @classmethod
    def poll(cls, context):
        """Function `poll` used by the NodeForge addon."""
        space = getattr(context, "space_data", None)
        return bool(space and getattr(space, "type", None) == 'NODE_EDITOR')

    def draw(self, context):
        """Function `draw` used by the NodeForge addon."""
        layout = self.layout
        props = context.scene.gn_script_mvp
        layout.label(text="Scripts → base Geometry Nodes tree")
        layout.prop(props, "text_block")
        if props.text_block is None:
            layout.label(text="Select a Blender Text datablock", icon='INFO')
        row = layout.row(align=True)
        op = row.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="Compile Script")
        op.expression = ""
        owner = getattr(context, "active_object", None)
        if owner is not None and getattr(owner, "type", None) == "MESH":
            row = layout.row()
            row.enabled = props.text_block is not None
            row.operator(NODEFORGE_OT_install_mesh_compute_modifier.bl_idname,
                         text="Install Mesh Compute", icon='NODETREE')
        if owner is not None and getattr(owner, "type", None) == "MESH" and owner.modifiers:
            from .blender.mesh_compute_runtime import get_modifier_binding
            try:
                stage = get_modifier_binding(owner)
            except ValueError:
                stage = None
            if stage is None:
                from .blender.mesh_compute_runtime import saved_runtime_status
                saved = saved_runtime_status(owner)
                if saved:
                    layout.label(text=f"Mesh Compute: {saved}", icon='ERROR')
                    layout.label(text='Python callback requires trusted Reload', icon='INFO')
                    layout.operator(NODEFORGE_OT_reload_mesh_compute_modifier.bl_idname,
                                    text='Reload Compute', icon='FILE_REFRESH')
            if stage is not None:
                layout.label(text=f"Mesh Compute: {stage.status}", icon='CHECKMARK' if stage.status == 'READY' else 'ERROR')
                layout.operator(NODEFORGE_OT_reload_mesh_compute_modifier.bl_idname,
                                text="Reload Compute", icon='FILE_REFRESH')
                if stage.last_error:
                    layout.label(text=stage.last_error[:100], icon='ERROR')
        selected_group = _selected_group_node(context)
        row = layout.row()
        row.enabled = selected_group is not None
        row.operator(NODEFORGE_OT_reload_selected_library_group.bl_idname, text="Reload Function from Source", icon='FILE_REFRESH')
        embedded_source = _extract_group_source(selected_group.node_tree) if selected_group is not None else ""
        row = layout.row()
        row.enabled = bool(embedded_source)
        row.operator(GNSCRIPT_MVP_OT_load_selected_group_source.bl_idname, text="Load Script From Selected NodeGroup")
        if selected_group is None:
            layout.label(text="Select a Group node to update/load", icon='INFO')
        elif not embedded_source:
            layout.label(text="Selected Group has no embedded NodeForge source", icon='INFO')


class NODEFORGE_PT_library(Panel):
    """Top-level collapsible panel for NodeForge catalog libraries."""
    bl_label = "Library"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "NodeForge"
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return GNSCRIPT_MVP_PT_panel.poll(context)

    def draw(self, context):
        self.layout.label(text="Save, browse, and add catalog node groups.", icon='ASSET_MANAGER')




class NODEFORGE_PT_library_local(Panel):
    """Collapsible Local catalog panel."""
    bl_label = "Local"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "NodeForge"
    bl_parent_id = "NODEFORGE_PT_library"
    bl_order = 0
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return GNSCRIPT_MVP_PT_panel.poll(context)

    def draw(self, context):
        layout = self.layout
        props = context.scene.gn_script_mvp
        nav = layout.row(align=True)
        back = nav.operator(NODEFORGE_OT_local_browser_back.bl_idname, text="", icon='BACK')
        nav.enabled = bool(props.local_browser_path)
        current = Path(props.local_browser_path).name if props.local_browser_path else "Local"
        nav.label(text=current, icon='FILE_FOLDER')
        row = layout.row(align=True)
        row.operator(NODEFORGE_OT_create_local_folder.bl_idname, text="New Folder", icon='NEWFOLDER')
        row.operator(NODEFORGE_OT_save_to_local.bl_idname, text="Save", icon='FILE_TICK')
        row.operator(NODEFORGE_OT_import_local.bl_idname, text="Add Folder...", icon='NEWFOLDER')
        _draw_library_catalog_panel(layout, context, "local", "local_items", "local_index", rows=7)



class NODEFORGE_PT_library_functions(Panel):
    """Collapsible Functions catalog panel."""
    bl_label = "Functions"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "NodeForge"
    bl_parent_id = "NODEFORGE_PT_library"
    bl_order = 1
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return GNSCRIPT_MVP_PT_panel.poll(context)

    def draw(self, context):
        _draw_library_catalog_panel(self.layout, context, "functions", "function_items", "function_index", rows=4)


class NODEFORGE_PT_library_examples(Panel):
    """Collapsible Examples catalog panel."""
    bl_label = "Examples"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "NodeForge"
    bl_parent_id = "NODEFORGE_PT_library"
    bl_order = 2
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return GNSCRIPT_MVP_PT_panel.poll(context)

    def draw(self, context):
        _draw_library_catalog_panel(self.layout, context, "examples", "example_items", "example_index", rows=3)



class NODEFORGE_PT_library_packages(Panel):
    """Collapsible installed packages panel."""

    bl_label = "Packages"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "NodeForge"
    bl_parent_id = "NODEFORGE_PT_library"
    bl_order = 3
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return GNSCRIPT_MVP_PT_panel.poll(context)

    def draw(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        layout = self.layout
        if props is None:
            layout.label(text="NodeForge properties unavailable", icon='ERROR')
            return
        row = layout.row(align=True)
        row.operator(NODEFORGE_OT_refresh_packages.bl_idname, text="Refresh", icon='FILE_REFRESH')
        row.operator(NODEFORGE_OT_import_package.bl_idname, text="Import", icon='IMPORT')
        layout.template_list(NODEFORGE_UL_packages.__name__, "", props, "package_items", props, "package_index", rows=3)
        if _PACKAGE_BOOTSTRAP_ERRORS:
            layout.label(text="Package initialization failed; see console", icon='ERROR')
        selected = _selected_package_item(props)
        if selected is not None and selected.compatibility_warning:
            layout.label(text=selected.compatibility_warning, icon='INFO')
        row = layout.row(align=True)
        op = row.operator(NODEFORGE_OT_uninstall_package.bl_idname, text="Uninstall", icon='TRASH')
        op.package_id = selected.package_id if selected is not None else ""


def menu_func(self, context):
    """Function `menu_func` used by the NodeForge addon."""
    space = getattr(context, "space_data", None)
    tree = _base_gn_tree(context)
    if tree is not None:
        self.layout.separator()
        op = self.layout.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="NodeForge: Compile Script")
        op.expression = ""

classes = (
    NODEFORGE_OT_reload_addon,
    NODEFORGE_AddonPreferences,
    NODEFORGE_FunctionItem,
    NODEFORGE_PackageItem,
    GNSCRIPT_MVP_Properties,
    NODEFORGE_UL_function_library,
    NODEFORGE_UL_packages,
    GNSCRIPT_MVP_OT_compile_expression,
    NODEFORGE_OT_install_mesh_compute_modifier,
    NODEFORGE_OT_reload_mesh_compute_modifier,
    NODEFORGE_OT_detach_selected_source_owner,
    NODEFORGE_OT_reload_selected_library_group,
    GNSCRIPT_MVP_OT_load_selected_group_source,
    NODEFORGE_OT_refresh_function_library,
    NODEFORGE_OT_refresh_packages,
    NODEFORGE_OT_import_package,
    NODEFORGE_OT_uninstall_package,
    NODEFORGE_OT_create_function_group,
    NODEFORGE_OT_open_local_folder,
    NODEFORGE_OT_local_browser_back,
    NODEFORGE_OT_import_local,
    NODEFORGE_OT_delete_local_file,
    NODEFORGE_OT_delete_local_folder,
    NODEFORGE_OT_remove_local_source,
    NODEFORGE_OT_create_local_folder,
    NODEFORGE_OT_save_to_local,
    GNSCRIPT_MVP_PT_panel,
    NODEFORGE_PT_library,
    NODEFORGE_PT_library_packages,
    NODEFORGE_PT_library_local,
    NODEFORGE_PT_library_functions,
    NODEFORGE_PT_library_examples,
)

_PACKAGE_BOOTSTRAP_ERRORS: tuple[str, ...] = ()


def _refresh_startup_browser_items():
    """Fill Scene-owned browser lists after Blender releases registration restrictions."""
    for scene in bpy.data.scenes:
        props = getattr(scene, "gn_script_mvp", None)
        if props is None:
            continue
        for namespace in ("packages", "functions", "examples"):
            try:
                if namespace == "packages":
                    _refresh_package_items(props)
                else:
                    _refresh_catalog_items(props, namespace)
            except Exception as exc:
                # One broken catalog cannot prevent other lists from loading.
                print(f"NodeForge {namespace} catalog initialization: {exc}")
    return None  # One-shot timer; every activation schedules a fresh refresh.


# A process-local ledger covers only registrations made by this UI module;
# Blender datablocks are never add-on lifecycle ownership records.
_REGISTERED_CLASSES: list[type] = []
_SCENE_PROP_REGISTERED = False
_ADD_MENU_REGISTERED = False
_RUNTIME_LOAD_REGISTERED = False
_EDITOR_REGISTRATION_ATTEMPTED = False
_UI_ENABLED = False


def register():
    """Install NodeForge UI once, undoing successful partial steps on failure."""
    global _PACKAGE_BOOTSTRAP_ERRORS, _SCENE_PROP_REGISTERED
    global _ADD_MENU_REGISTERED, _RUNTIME_LOAD_REGISTERED
    global _EDITOR_REGISTRATION_ATTEMPTED, _UI_ENABLED
    if _UI_ENABLED:
        return
    try:
        generated_resources.cleanup_restart_orphans_deferred()
        from .blender.mesh_compute_runtime import register_runtime_load_handler
        register_runtime_load_handler()
        _RUNTIME_LOAD_REGISTERED = True
        for cls in classes:
            bpy.utils.register_class(cls)
            _REGISTERED_CLASSES.append(cls)
        bpy.types.Scene.gn_script_mvp = PointerProperty(type=GNSCRIPT_MVP_Properties)
        _SCENE_PROP_REGISTERED = True
        bpy.types.NODE_MT_add.append(menu_func)
        _ADD_MENU_REGISTERED = True
        try:
            _PACKAGE_BOOTSTRAP_ERRORS = packages.install_included_packages(Path(__file__).resolve().parent)
        except Exception as exc:
            _PACKAGE_BOOTSTRAP_ERRORS = (str(exc),)
        for message in _PACKAGE_BOOTSTRAP_ERRORS:
            print(f"NodeForge package initialization: {message}")
        # Browser data is unavailable in some Blender registration contexts.
        if not bpy.app.timers.is_registered(_refresh_startup_browser_items):
            bpy.app.timers.register(_refresh_startup_browser_items, first_interval=0.0)
        from .editor import blender_ui
        _EDITOR_REGISTRATION_ATTEMPTED = True
        blender_ui.register()
        _UI_ENABLED = True
    except BaseException:
        unregister()
        raise


def unregister():
    """Release only our UI registration state; preserve generated datablocks."""
    global _SCENE_PROP_REGISTERED, _ADD_MENU_REGISTERED
    global _RUNTIME_LOAD_REGISTERED, _EDITOR_REGISTRATION_ATTEMPTED, _UI_ENABLED
    if not (_UI_ENABLED or _REGISTERED_CLASSES or _SCENE_PROP_REGISTERED
            or _ADD_MENU_REGISTERED or _RUNTIME_LOAD_REGISTERED
            or _EDITOR_REGISTRATION_ATTEMPTED):
        return
    _UI_ENABLED = False
    if bpy.app.timers.is_registered(_refresh_startup_browser_items):
        bpy.app.timers.unregister(_refresh_startup_browser_items)
    if _EDITOR_REGISTRATION_ATTEMPTED:
        from .editor import blender_ui
        blender_ui.unregister()
        _EDITOR_REGISTRATION_ATTEMPTED = False
    if _ADD_MENU_REGISTERED:
        bpy.types.NODE_MT_add.remove(menu_func)
        _ADD_MENU_REGISTERED = False
    if _SCENE_PROP_REGISTERED:
        del bpy.types.Scene.gn_script_mvp
        _SCENE_PROP_REGISTERED = False
    while _REGISTERED_CLASSES:
        cls = _REGISTERED_CLASSES[-1]
        bpy.utils.unregister_class(cls)
        _REGISTERED_CLASSES.pop()
    if _RUNTIME_LOAD_REGISTERED:
        from .blender.mesh_compute_runtime import (
            shutdown_runtime_bindings, unregister_runtime_load_handler,
        )
        shutdown_runtime_bindings()
        unregister_runtime_load_handler()
        _RUNTIME_LOAD_REGISTERED = False

if __name__ == "__main__":
    register()
