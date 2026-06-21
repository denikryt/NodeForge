"""Blender UI, operators, panels, and registration for NodeForge."""

import bpy
import sys
import importlib
import addon_utils
from bpy.types import Operator, Panel, PropertyGroup, UIList, AddonPreferences
from bpy.props import StringProperty, PointerProperty, CollectionProperty, IntProperty

from .compiler import (
    create_expression_group,
    update_expression_group,
    _apply_group_defaults_to_node,
    _capture_node_external_state,
    _restore_node_external_state,
    _extract_group_source,
    _get_or_create_scratch_text,
    _replace_text_contents,
    create_library_function_group,
)
from .library import library_function_records, apply_function_node_display_name

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
    """One row in the NodeForge function-library UI list."""
    name: StringProperty(name="Name", default="")
    kind: StringProperty(name="Kind", default="")
    path: StringProperty(name="Path", default="")


class NODEFORGE_UL_function_library(UIList):
    """Draw available function-library entries in the N-panel."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        """Draw one discovered function entry."""
        row = layout.row(align=True)
        row.label(text=item.name, icon='NODETREE')


def _refresh_function_items(props):
    """Reload the functions folder into the Scene collection property."""
    old_name = ""
    if 0 <= props.function_index < len(props.function_items):
        old_name = props.function_items[props.function_index].name
    props.function_items.clear()
    records = library_function_records()
    for record in records:
        item = props.function_items.add()
        item.name = record.get("name", "")
        item.kind = record.get("kind", "")
        item.path = record.get("path", "")
    props.function_index = 0
    if old_name:
        for index, item in enumerate(props.function_items):
            if item.name == old_name:
                props.function_index = index
                break
    return len(records)


def _selected_function_item(props):
    """Return the currently selected function-library item, or None."""
    if props is None:
        return None
    if 0 <= props.function_index < len(props.function_items):
        return props.function_items[props.function_index]
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
    """Refresh the list of functions discovered in NodeForge/functions."""
    bl_idname = "nodeforge.refresh_function_library"
    bl_label = "Refresh Function Library"
    bl_description = "Reload available functions from the NodeForge/functions folder"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """Reload function-library entries into the N-panel list."""
        props = getattr(context.scene, "gn_script_mvp", None)
        if props is None:
            self.report({'ERROR'}, "NodeForge properties are not available")
            return {'CANCELLED'}
        count = _refresh_function_items(props)
        self.report({'INFO'}, f"Found {count} function(s)")
        return {'FINISHED'}


class NODEFORGE_OT_create_function_group(Operator):
    """Insert the selected function-library entry into the active Geometry Nodes editor."""
    bl_idname = "nodeforge.create_function_group"
    bl_label = "Add Function Node Group"
    bl_description = "Create the selected function node group if needed and insert it into the active Geometry Nodes editor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Enable only when a function entry is selected."""
        props = getattr(context.scene, "gn_script_mvp", None)
        return _selected_function_item(props) is not None

    def execute(self, context):
        """Materialize the selected function and insert it as a group node."""
        props = getattr(context.scene, "gn_script_mvp", None)
        item = _selected_function_item(props)
        if item is None:
            self.report({'ERROR'}, "Select a function from the list")
            return {'CANCELLED'}

        tree = _active_gn_tree(context)
        if tree is None:
            self.report({'ERROR'}, "Open a Geometry Nodes editor to add a function node group")
            return {'CANCELLED'}

        try:
            group = create_library_function_group(item.name)
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
        self.report({'INFO'}, f"Added function node group: {group.name}")
        return {'FINISHED'}

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

        layout.separator()
        header = layout.row(align=True)
        header.label(text="Function Library")
        header.operator(NODEFORGE_OT_refresh_function_library.bl_idname, text="", icon='FILE_REFRESH')
        if not props.function_items:
            _refresh_function_items(props)
        layout.template_list(
            NODEFORGE_UL_function_library.__name__,
            "",
            props,
            "function_items",
            props,
            "function_index",
            rows=4,
        )
        item = _selected_function_item(props)
        row = layout.row(align=True)
        row.enabled = item is not None and _active_gn_tree(context) is not None
        row.operator(NODEFORGE_OT_create_function_group.bl_idname, text="Add Node Group", icon='NODETREE')
        if _active_gn_tree(context) is None:
            layout.label(text="Open a Geometry Nodes editor to add", icon='INFO')


def menu_func(self, context):
    """Function `menu_func` used by the NodeForge addon."""
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None) if space else None
    if tree and getattr(tree, "bl_idname", None) == "GeometryNodeTree":
        self.layout.separator()
        op = self.layout.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="NodeForge: Compile Group")
        op.expression = ""
        op.insert_node = True

classes = (NODEFORGE_OT_reload_addon, NODEFORGE_AddonPreferences, NODEFORGE_FunctionItem, GNSCRIPT_MVP_Properties, NODEFORGE_UL_function_library, GNSCRIPT_MVP_OT_compile_expression, GNSCRIPT_MVP_OT_update_selected_group, GNSCRIPT_MVP_OT_load_selected_group_source, NODEFORGE_OT_refresh_function_library, NODEFORGE_OT_create_function_group, GNSCRIPT_MVP_PT_panel)

def register():
    """Function `register` used by the NodeForge addon."""
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gn_script_mvp = PointerProperty(type=GNSCRIPT_MVP_Properties)
    bpy.types.NODE_MT_add.append(menu_func)

def unregister():
    """Function `unregister` used by the NodeForge addon."""
    try:
        bpy.types.NODE_MT_add.remove(menu_func)
    except Exception:
        pass
    if hasattr(bpy.types.Scene, "gn_script_mvp"):
        del bpy.types.Scene.gn_script_mvp
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
