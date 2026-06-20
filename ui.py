"""Blender UI, operators, panels, and registration for GN Script MVP."""

import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import StringProperty, PointerProperty

from .compiler import (
    create_expression_group,
    update_expression_group,
    _apply_group_defaults_to_node,
    _capture_node_external_state,
    _restore_node_external_state,
    _extract_group_source,
    _get_or_create_scratch_text,
    _replace_text_contents,
)

def _source_from_props(props):
    """Function `_source_from_props` used by the GN Script MVP addon."""
    if props and props.text_block:
        return props.text_block.as_string()
    return props.expression if props else ""


def _active_gn_tree(context):
    """Function `_active_gn_tree` used by the GN Script MVP addon."""
    space = getattr(context, "space_data", None)
    if space and getattr(space, "type", None) == 'NODE_EDITOR':
        tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
        if tree and getattr(tree, "bl_idname", None) == "GeometryNodeTree":
            return tree
    return None

def _selected_group_node(context):
    """Function `_selected_group_node` used by the GN Script MVP addon."""
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

class GNSCRIPT_MVP_Properties(PropertyGroup):
    """Class `GNSCRIPT_MVP_Properties` used by the GN Script MVP addon."""
    expression: StringProperty(
        name="Script",
        description="Small Python-like script compiled into Geometry Nodes. For multi-line editing, use the optional Text datablock below.",
        default="p = position(); h = sin(p.x * freq) * cos(p.y * freq) * amp; out = p + vector(0, 0, h)",
    )
    text_block: PointerProperty(
        name="Text Script",
        description="Optional Blender Text datablock. If set, this overrides the inline Script field.",
        type=bpy.types.Text,
    )

class GNSCRIPT_MVP_OT_compile_expression(Operator):
    """Class `GNSCRIPT_MVP_OT_compile_expression` used by the GN Script MVP addon."""
    bl_idname = "gn_script_mvp.compile_expression"
    bl_label = "Compile GN Script Group"
    bl_description = "Compile a small Python-like script into a Geometry Nodes group"
    bl_options = {'REGISTER', 'UNDO'}

    expression: StringProperty(default="")
    insert_node: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        """Function `poll` used by the GN Script MVP addon."""
        return True

    def execute(self, context):
        """Function `execute` used by the GN Script MVP addon."""
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
    """Class `GNSCRIPT_MVP_OT_update_selected_group` used by the GN Script MVP addon."""
    bl_idname = "gn_script_mvp.update_selected_group"
    bl_label = "Update Selected NodeGroup"
    bl_description = "Recompile the current script into the selected Geometry Node Group, preserving the group datablock and name"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Function `poll` used by the GN Script MVP addon."""
        return _selected_group_node(context) is not None

    def execute(self, context):
        """Function `execute` used by the GN Script MVP addon."""
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
    """Class `GNSCRIPT_MVP_OT_load_selected_group_source` used by the GN Script MVP addon."""
    bl_idname = "gn_script_mvp.load_selected_group_source"
    bl_label = "Load Script From Selected NodeGroup"
    bl_description = "Load the embedded GN Script source from the selected Geometry Node Group into the editor field/Text datablock"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Function `poll` used by the GN Script MVP addon."""
        node = _selected_group_node(context)
        return node is not None and bool(_extract_group_source(node.node_tree))

    def execute(self, context):
        """Function `execute` used by the GN Script MVP addon."""
        props = getattr(context.scene, "gn_script_mvp", None)
        node = _selected_group_node(context)
        if node is None:
            self.report({'ERROR'}, "Select exactly one Geometry Node Group node")
            return {'CANCELLED'}
        source = _extract_group_source(node.node_tree)
        if not source:
            self.report({'ERROR'}, "Selected node group has no embedded GN Script source")
            return {'CANCELLED'}
        if props is None:
            self.report({'ERROR'}, "GN Script MVP properties are not available")
            return {'CANCELLED'}
        text = props.text_block or _get_or_create_scratch_text()
        _replace_text_contents(text, source)
        props.text_block = text
        props.expression = source
        self.report({'INFO'}, f"Loaded script from: {node.node_tree.name}")
        return {'FINISHED'}

class GNSCRIPT_MVP_PT_panel(Panel):
    """Class `GNSCRIPT_MVP_PT_panel` used by the GN Script MVP addon."""
    bl_label = "GN Script MVP"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "GN Script"

    @classmethod
    def poll(cls, context):
        """Function `poll` used by the GN Script MVP addon."""
        space = getattr(context, "space_data", None)
        return bool(space and getattr(space, "type", None) == 'NODE_EDITOR')

    def draw(self, context):
        """Function `draw` used by the GN Script MVP addon."""
        layout = self.layout
        props = context.scene.gn_script_mvp
        layout.label(text="Python-like script → Node Group")
        layout.prop(props, "text_block")
        layout.prop(props, "expression", text="")
        op = layout.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="Compile Script")
        op.expression = "" if props.text_block else props.expression
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
            layout.label(text="Selected Group has no embedded GN Script", icon='INFO')
        layout.separator()
        layout.label(text="Examples:")
        layout.label(text="out = clamp(sin(x), 0, 1)")
        layout.label(text="p = position(); out = p + vector(0,0,1)")
        layout.label(text="set_position(position() + vector(0,0,amp))")

def menu_func(self, context):
    """Function `menu_func` used by the GN Script MVP addon."""
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None) if space else None
    if tree and getattr(tree, "bl_idname", None) == "GeometryNodeTree":
        self.layout.separator()
        op = self.layout.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="GN Script: Compile Group")
        props = getattr(context.scene, "gn_script_mvp", None)
        op.expression = props.expression if props else "out = sin(x * 10) + cos(y * 10)"
        op.insert_node = True

classes = (GNSCRIPT_MVP_Properties, GNSCRIPT_MVP_OT_compile_expression, GNSCRIPT_MVP_OT_update_selected_group, GNSCRIPT_MVP_OT_load_selected_group_source, GNSCRIPT_MVP_PT_panel)

def register():
    """Function `register` used by the GN Script MVP addon."""
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gn_script_mvp = PointerProperty(type=GNSCRIPT_MVP_Properties)
    bpy.types.NODE_MT_add.append(menu_func)

def unregister():
    """Function `unregister` used by the GN Script MVP addon."""
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
