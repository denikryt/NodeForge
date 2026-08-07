"""Blender UI, operators, panels, and registration for NodeForge."""

import bpy
import sys
import importlib
import addon_utils
from pathlib import Path
from bpy.types import Operator, Panel, PropertyGroup, UIList, AddonPreferences, OperatorFileListElement
from bpy.props import StringProperty, PointerProperty, CollectionProperty, IntProperty, BoolProperty, EnumProperty

from .compiler import (
    create_expression_group,
    update_expression_group,
    _apply_group_defaults_to_node,
    _capture_node_external_state,
    _restore_node_external_state,
    _extract_group_source,
    _get_or_create_scratch_text,
    _replace_text_contents,
    create_library_catalog_group,
    create_library_function_group,
)
from .library import library_entry_records, library_function_records, apply_function_node_display_name, create_local_folder, save_local_source
from . import packages
from . import generated_resources

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
    bl_idname = __package__.split(".")[0] if __package__ else "NodeForge"

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




class NODEFORGE_PackageItem(PropertyGroup):
    """One installed NodeForge package row shown in the UI."""

    id: StringProperty(name="ID", default="")
    package_id: StringProperty(name="Package ID", default="")
    name: StringProperty(name="Name", default="")
    version: StringProperty(name="Version", default="")
    origin: StringProperty(name="Origin", default="")
    status: StringProperty(name="Status", default="active")
    path: StringProperty(name="Path", default="")
    python_required: BoolProperty(name="Python Required", default=False)
    python_allowed: BoolProperty(name="Python Allowed", default=False)
    invalid_reason: StringProperty(name="Invalid Reason", default="")


class NODEFORGE_UL_function_library(UIList):
    """Draw available function-library entries in the N-panel."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        """Draw one discovered function entry."""
        row = layout.row(align=True)
        label = item.name if not getattr(item, "folder_path", "") else f"{item.folder_path}/{item.name}"
        row.label(text=label, icon='NODETREE')
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
        icon_name = 'ERROR' if item.invalid_reason else 'PACKAGE'
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
            item.status = "python blocked" if record.python_required and not record.python_allowed else "invalid"
            item.path = record.path
            item.python_required = record.python_required
            item.python_allowed = record.python_allowed
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
        item.python_required = record.python_required
        item.python_allowed = bool(record.state_record.get("allow_python", False))
        item.invalid_reason = ""
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
    if 0 <= old_index < len(items):
        old_name = items[old_index].name
    items.clear()
    records = library_entry_records(namespace)
    for record in records:
        item = items.add()
        item.name = record.get("name", "")
        item.kind = record.get("kind", "")
        item.path = record.get("path", "")
        item.folder_path = record.get("folder_path", "")
        if hasattr(item, "package_id"):
            item.package_id = record.get("package_id", "")
            item.package_name = record.get("package_name", "")
    setattr(props, index_prop, 0)
    if old_name:
        for index, item in enumerate(items):
            if item.name == old_name:
                setattr(props, index_prop, index)
                break
    return len(records)


def _refresh_function_items(props):
    """Reload the functions folder into the Scene collection property."""
    return _refresh_catalog_items(props, "functions")


def _selected_catalog_item(props, namespace: str):
    """Return the selected catalog-library item, or None."""
    if props is None:
        return None
    items, index_prop = _items_for_namespace(props, namespace)
    index = getattr(props, index_prop)
    if 0 <= index < len(items):
        return items[index]
    return None


def _selected_function_item(props):
    """Return the currently selected function-library item, or None."""
    return _selected_catalog_item(props, "functions")

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
    package_allow_python: BoolProperty(name="Allow executable Python", default=False)
    local_script_name: StringProperty(name="Script Name", default="")
    local_folder_path: StringProperty(name="Folder", default="")
    local_overwrite: BoolProperty(name="Overwrite", default=False)
    local_source_kind: EnumProperty(
        name="Source",
        description="Source to save into the local catalog",
        items=(
            ("TEXT", "Text Block", "Save the selected Blender Text datablock"),
            ("SELECTED_GROUP", "Selected Group", "Save the embedded source from the selected generated Node Group"),
        ),
        default="TEXT",
    )

class GNSCRIPT_MVP_OT_compile_expression(Operator):
    """Class `GNSCRIPT_MVP_OT_compile_expression` used by the NodeForge addon."""
    bl_idname = "gn_script_mvp.compile_expression"
    bl_label = "Compile NodeForge Group"
    bl_description = "Compile a small Python-like script into a Geometry Nodes group"
    bl_options = {'REGISTER', 'UNDO'}

    expression: StringProperty(default="")
    insert_node: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        """Function `poll` used by the NodeForge addon."""
        return True

    def execute(self, context):
        """Function `execute` used by the NodeForge addon."""
        props = getattr(context.scene, "gn_script_mvp", None)
        source = self.expression or _source_from_props(props)
        try:
            group = create_expression_group(source)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        tree = _active_gn_tree(context)
        if self.insert_node and tree is not None:
            node = tree.nodes.new("GeometryNodeGroup")
            node.node_tree = group
            _apply_group_defaults_to_node(node)
            node.location = (0, 0)
            tree.nodes.active = node
            node.select = True
        self.report({'INFO'}, f"Created node group: {group.name}")
        return {'FINISHED'}


class GNSCRIPT_MVP_OT_update_selected_group(Operator):
    """Class `GNSCRIPT_MVP_OT_update_selected_group` used by the NodeForge addon."""
    bl_idname = "gn_script_mvp.update_selected_group"
    bl_label = "Update Selected NodeGroup"
    bl_description = "Recompile the current script into the selected Geometry Node Group, preserving the group datablock and name"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Function `poll` used by the NodeForge addon."""
        return _selected_group_node(context) is not None

    def execute(self, context):
        """Function `execute` used by the NodeForge addon."""
        props = getattr(context.scene, "gn_script_mvp", None)
        source = _source_from_props(props)
        node = _selected_group_node(context)
        if node is None:
            self.report({'ERROR'}, "Select exactly one Geometry Node Group node to update")
            return {'CANCELLED'}
        old_name = node.node_tree.name
        tree = _active_gn_tree(context)
        external_state = _capture_node_external_state(tree, node) if tree is not None else {"input_defaults": {}, "incoming": [], "outgoing": []}
        try:
            update_expression_group(node.node_tree, source)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        node.node_tree.name = old_name
        node.name = node.name or old_name
        restored = _restore_node_external_state(tree, node, external_state) if tree is not None else 0
        self.report({'INFO'}, f"Updated node group: {old_name}; restored {restored} link(s)")
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
    allow_python: BoolProperty(name="Allow executable Python", default=False)
    replace: BoolProperty(name="Replace existing package", default=False)

    def invoke(self, context, event):
        """Open Blender's file selector for zip package import."""
        props = getattr(context.scene, "gn_script_mvp", None)
        if props is not None:
            self.filepath = props.package_archive_path
            self.allow_python = props.package_allow_python
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        """Install the selected package archive and refresh package/catalog UI."""
        props = getattr(context.scene, "gn_script_mvp", None)
        try:
            manifest = packages.install_package_zip(self.filepath, allow_python=self.allow_python, replace=self.replace)
            if props is not None:
                props.package_archive_path = self.filepath
                props.package_allow_python = self.allow_python
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
    bl_description = "Create the selected library node group if needed and insert it into the active Geometry Nodes editor"
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

        tree = _active_gn_tree(context)
        if tree is None:
            self.report({'ERROR'}, "Open a Geometry Nodes editor to add a library node group")
            return {'CANCELLED'}

        try:
            group = create_library_catalog_group(self.namespace, item.name)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

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


class NODEFORGE_OT_import_local_scripts(Operator):
    """Import one or more .nf files into the persistent Local catalog."""

    bl_idname = "nodeforge.import_local_scripts"
    bl_label = "Import Local Scripts"
    bl_description = "Import one or more .nf scripts into the persistent NodeForge Local catalog"
    bl_options = {'REGISTER'}

    directory: StringProperty(subtype="DIR_PATH", default="")
    files: CollectionProperty(type=OperatorFileListElement)
    filter_glob: StringProperty(default="*.nf", options={'HIDDEN'})
    folder_path: StringProperty(name="Local Folder", default="")
    replace_existing: BoolProperty(name="Replace Existing", default=False)

    def invoke(self, context, event):
        props = getattr(context.scene, "gn_script_mvp", None)
        if props is not None:
            self.folder_path = props.local_folder_path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        selected = [Path(self.directory) / item.name for item in self.files]
        if not selected:
            self.report({'ERROR'}, "Select at least one .nf file")
            return {'CANCELLED'}

        try:
            prepared = []
            seen_names = set()
            for path in selected:
                if path.suffix.lower() != ".nf":
                    raise ValueError(f"Not a NodeForge .nf script: {path.name}")
                name = path.stem
                # save_local_source performs authoritative public-name validation.
                if name in seen_names:
                    raise ValueError(f"Duplicate script name in selection: {name!r}")
                seen_names.add(name)
                source = path.read_text(encoding="utf-8")
                if not source.strip():
                    raise ValueError(f"Local script is empty: {path.name}")
                existing = next((r for r in library_entry_records("local") if r.get("name") == name), None)
                if existing is not None and not self.replace_existing:
                    raise ValueError(f"Local script {name!r} already exists")
                prepared.append((name, source, existing is not None))

            imported = []
            for name, source, exists in prepared:
                imported.append(save_local_source(
                    name,
                    source,
                    folder_path=self.folder_path,
                    overwrite=bool(exists),
                ))

            if props is not None:
                props.local_folder_path = self.folder_path
                _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        self.report({'INFO'}, f"Imported {len(imported)} local script(s)")
        return {'FINISHED'}


class NODEFORGE_OT_create_local_folder(Operator):
    """Create a validated folder under the user-owned local catalog."""
    bl_idname = "nodeforge.create_local_folder"
    bl_label = "New Local Folder"
    bl_description = "Create a folder inside the persistent NodeForge local catalog for organizing local scripts"
    bl_options = {'REGISTER'}

    folder_path: StringProperty(name="Folder", default="")
    package_id: StringProperty(name="Package ID", default="")
    package_name: StringProperty(name="Package", default="")

    def invoke(self, context, event):
        """Open Blender's normal operator-property dialog."""
        props = getattr(context.scene, "gn_script_mvp", None)
        if props is not None and not self.folder_path:
            self.folder_path = props.local_folder_path
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        """Create the folder and refresh the Local list."""
        props = getattr(context.scene, "gn_script_mvp", None)
        try:
            create_local_folder(self.folder_path)
            if props is not None:
                props.local_folder_path = self.folder_path
                _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Created local folder: {self.folder_path}")
        return {'FINISHED'}


def _source_for_local_save(context, source_kind):
    """Return source from exactly the user-selected local-save source kind."""
    props = getattr(context.scene, "gn_script_mvp", None)
    if source_kind == "TEXT":
        source = _source_from_props(props)
        if not source:
            raise ValueError("Select a Blender Text datablock to save")
        return source
    if source_kind == "SELECTED_GROUP":
        node = _selected_group_node(context)
        if node is None:
            raise ValueError("Select exactly one generated Node Group to save")
        source = _extract_group_source(node.node_tree)
        if not source:
            raise ValueError("Selected Node Group has no embedded NodeForge source")
        return source
    raise ValueError(f"Unknown local save source kind: {source_kind}")


class NODEFORGE_OT_save_to_local(Operator):
    """Save a Text datablock or selected NodeForge group source into the persistent local catalog."""
    bl_idname = "nodeforge.save_to_local"
    bl_label = "Save to Local"
    bl_description = "Save the selected Text script or selected NodeForge node group source into the persistent NodeForge local catalog"
    bl_options = {'REGISTER'}

    source_kind: EnumProperty(
        name="Source",
        description="Choose the source to save into the persistent local catalog",
        items=(
            ("TEXT", "Text Block", "Save the selected Blender Text datablock"),
            ("SELECTED_GROUP", "Selected Group", "Save the embedded source from the selected generated Node Group"),
        ),
        default="TEXT",
    )
    name: StringProperty(name="Script Name", default="")
    folder_path: StringProperty(name="Folder", default="")
    package_id: StringProperty(name="Package ID", default="")
    package_name: StringProperty(name="Package", default="")
    overwrite: BoolProperty(name="Overwrite", default=False)

    def invoke(self, context, event):
        """Open Blender's normal operator-property dialog."""
        props = getattr(context.scene, "gn_script_mvp", None)
        if props is not None:
            self.name = self.name or props.local_script_name
            self.folder_path = self.folder_path or props.local_folder_path
            self.overwrite = bool(props.local_overwrite)
            self.source_kind = props.local_source_kind
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        """Write the source to a validated local file and refresh Local."""
        props = getattr(context.scene, "gn_script_mvp", None)
        try:
            source = _source_for_local_save(context, self.source_kind)
            path = save_local_source(self.name, source, folder_path=self.folder_path, overwrite=self.overwrite)
            if props is not None:
                props.local_script_name = self.name
                props.local_folder_path = self.folder_path
                props.local_overwrite = self.overwrite
                props.local_source_kind = self.source_kind
                _refresh_catalog_items(props, "local")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved local script: {path.name}")
        return {'FINISHED'}

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
    row.enabled = item is not None and _active_gn_tree(context) is not None
    op = row.operator(NODEFORGE_OT_create_function_group.bl_idname, text="Add Node Group", icon='NODETREE')
    op.namespace = namespace
    if _active_gn_tree(context) is None:
        layout.label(text="Open a Geometry Nodes editor to add", icon='INFO')


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
        layout.label(text="Python-like script → Node Group")
        layout.prop(props, "text_block")
        if props.text_block is None:
            layout.label(text="Select a Blender Text datablock", icon='INFO')
        op = layout.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="Compile Script")
        op.expression = ""
        op.insert_node = True
        row = layout.row()
        selected_group = _selected_group_node(context)
        row.enabled = selected_group is not None
        row.operator(GNSCRIPT_MVP_OT_update_selected_group.bl_idname, text="Update Selected NodeGroup")
        row = layout.row()
        row.enabled = selected_group is not None and bool(_extract_group_source(selected_group.node_tree))
        row.operator(GNSCRIPT_MVP_OT_load_selected_group_source.bl_idname, text="Load Script From Selected NodeGroup")
        if selected_group is None:
            layout.label(text="Select a Group node to update/load", icon='INFO')
        elif not _extract_group_source(selected_group.node_tree):
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
        row = layout.row(align=True)
        row.operator(NODEFORGE_OT_create_local_folder.bl_idname, text="New Folder", icon='NEWFOLDER')
        row.operator(NODEFORGE_OT_save_to_local.bl_idname, text="Save to Local", icon='FILE_TICK')
        row.operator(NODEFORGE_OT_import_local_scripts.bl_idname, text="Import", icon='IMPORT')
        _draw_library_catalog_panel(layout, context, "local", "local_items", "local_index", rows=3)


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
        layout.prop(props, "package_allow_python", text="Allow executable Python")
        row = layout.row(align=True)
        row.operator(NODEFORGE_OT_refresh_packages.bl_idname, text="Refresh", icon='FILE_REFRESH')
        row.operator(NODEFORGE_OT_import_package.bl_idname, text="Import", icon='IMPORT')
        layout.template_list(NODEFORGE_UL_packages.__name__, "", props, "package_items", props, "package_index", rows=3)
        selected = _selected_package_item(props)
        row = layout.row(align=True)
        op = row.operator(NODEFORGE_OT_uninstall_package.bl_idname, text="Uninstall", icon='TRASH')
        op.package_id = selected.package_id if selected is not None else ""


def menu_func(self, context):
    """Function `menu_func` used by the NodeForge addon."""
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None) if space else None
    if tree and getattr(tree, "bl_idname", None) == "GeometryNodeTree":
        self.layout.separator()
        op = self.layout.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="NodeForge: Compile Group")
        op.expression = ""
        op.insert_node = True

classes = (
    NODEFORGE_OT_reload_addon,
    NODEFORGE_AddonPreferences,
    NODEFORGE_FunctionItem,
    NODEFORGE_PackageItem,
    GNSCRIPT_MVP_Properties,
    NODEFORGE_UL_function_library,
    NODEFORGE_UL_packages,
    GNSCRIPT_MVP_OT_compile_expression,
    GNSCRIPT_MVP_OT_update_selected_group,
    GNSCRIPT_MVP_OT_load_selected_group_source,
    NODEFORGE_OT_refresh_function_library,
    NODEFORGE_OT_refresh_packages,
    NODEFORGE_OT_import_package,
    NODEFORGE_OT_uninstall_package,
    NODEFORGE_OT_create_function_group,
    NODEFORGE_OT_import_local_scripts,
    NODEFORGE_OT_create_local_folder,
    NODEFORGE_OT_save_to_local,
    GNSCRIPT_MVP_PT_panel,
    NODEFORGE_PT_library,
    NODEFORGE_PT_library_packages,
    NODEFORGE_PT_library_local,
    NODEFORGE_PT_library_functions,
    NODEFORGE_PT_library_examples,
)

def register():
    """Function `register` used by the NodeForge addon."""
    generated_resources.cleanup_restart_orphans_deferred()
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gn_script_mvp = PointerProperty(type=GNSCRIPT_MVP_Properties)
    bpy.types.NODE_MT_add.append(menu_func)

def unregister():
    """Function `unregister` used by the NodeForge addon."""
    try:
        generated_resources.cleanup_live_group_resources()
    except Exception:
        pass
    try:
        bpy.types.NODE_MT_add.remove(menu_func)
    except Exception:
        pass
    if hasattr(bpy.types.Scene, "gn_script_mvp"):
        del bpy.types.Scene.gn_script_mvp
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError as exc:
            if "missing bl_rna attribute" not in str(exc):
                raise

if __name__ == "__main__":
    register()
