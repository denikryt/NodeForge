"""Physical Blender 5.2 regressions for .nf occurrence-scoped inline compilation."""

from __future__ import annotations

import pytest

from helpers import *

pytestmark = pytest.mark.blender


def _compose(source, title):
    """Compile a source module into a fresh Geometry Nodes base tree."""
    tree = bpy.data.node_groups.new(title, 'GeometryNodeTree')
    compiler.compose_source_initial(source, tree)
    return tree


def _nodes(tree, name):
    """Return Blender nodes having one native RNA identifier."""
    return [node for node in tree.nodes if node.bl_idname == name]


def test_inline_single_raw_wrapper_native_nodes_and_shared_group():
    """Two inline occurrences create native nodes, while False stays grouped."""
    tree = _compose('''def merge_by_distance(geometry: Geometry, max_distance: Float = 0.001, __inline__=True) -> Geometry:
    return node('GeometryNodeMergeByDistance', inputs={'Geometry': geometry, 'Distance': max_distance}, output='Geometry', typ=Geometry)
base = cube(1.0)
one = merge_by_distance(base)
two = merge_by_distance(one, max_distance=0.02)
three = merge_by_distance(two, __inline__=False)
''', 'NFTest_Inline_Raw')
    assert len(_nodes(tree, 'GeometryNodeMergeByDistance')) == 2
    assert len(_nodes(tree, 'GeometryNodeGroup')) == 1
    assert len(_nodes(_nodes(tree, 'GeometryNodeGroup')[0].node_tree, 'GeometryNodeMergeByDistance')) == 1
    assert all(link.is_valid for link in tree.links)
    assert all(node.inputs['Geometry'].is_linked for node in _nodes(tree, 'GeometryNodeMergeByDistance'))


def test_inline_scalar_tuple_and_panel_reordered_parameters():
    """An occurrence binds panel-reordered inputs and returns a flat tuple."""
    tree = _compose('''def calculate(a: Float, b: Float, c: Float = 3, __inline__: Bool = True):
    panel([c, a], name='Controls')
    return (a + b, c + a)
first, second = calculate(1.0, 2.0)
result = first + second
''', 'NFTest_Inline_Tuple_Panel')
    assert not _nodes(tree, 'GeometryNodeGroup')
    assert len(_nodes(tree, 'ShaderNodeMath')) >= 3
    assert all(link.is_valid for link in tree.links)
    assert not any(item.item_type == 'PANEL' for item in tree.interface.items_tree)


def test_inline_typed_defaults_keep_color_alpha_and_float_conversion():
    """All static inputs are runtime nodes; Int fields widen to Float explicitly."""
    tree = _compose('''def float_identity(value: Float, __inline__=True) -> Float:
    return value

def color_identity(value: Color = (0.1, 0.2, 0.3, 0.4), __inline__=True) -> Color:
    return value
value = float_identity(index())
rgba = color_identity()
''', 'NFTest_Inline_Typed_Defaults')
    assert not _nodes(tree, 'GeometryNodeGroup')
    assert _nodes(tree, 'ShaderNodeMath')
    color_nodes = _nodes(tree, 'FunctionNodeInputColor')
    assert len(color_nodes) == 1
    assert tuple(color_nodes[0].value) == pytest.approx((0.1, 0.2, 0.3, 0.4), abs=1e-7)
    assert all(link.is_valid for link in tree.links)


def test_inline_call_scopes_do_not_alias_backing_nodes():
    """Repeated calls create independent native nodes without callee datablocks."""
    tree = _compose('''def increment(v: Float, __inline__=True) -> Float:
    return v + 1.0
x = increment(2.0)
y = increment(3.0)
z = x + y
''', 'NFTest_Inline_Occurrence_Scopes')
    assert not _nodes(tree, 'GeometryNodeGroup')
    assert len(_nodes(tree, 'ShaderNodeMath')) >= 3
    assert all(link.is_valid for link in tree.links)


def test_nested_group_outer_contains_native_inline_inner():
    """A grouped outer keeps its physical boundary, not the inline inner's."""
    root = _compose('''def inner(value: Float, __inline__=True) -> Float:
    return value + 1.0

def outer(value: Float) -> Float:
    return inner(value)
result = outer(2.0)
''', 'NFTest_Inline_Group_Outer')
    assert len(_nodes(root, 'GeometryNodeGroup')) == 1
    group = _nodes(root, 'GeometryNodeGroup')[0].node_tree
    assert len(_nodes(group, 'ShaderNodeMath')) == 1
    assert not _nodes(group, 'GeometryNodeGroup')


def test_nested_inline_outer_preserves_grouped_inner():
    """One inner group remains physically nested under an inline outer call."""
    root = _compose('''def inner(value: Float) -> Float:
    return value + 1.0

def outer(value: Float, __inline__=True) -> Float:
    return inner(value)
result = outer(2.0)
''', 'NFTest_Inline_Outer_Group_Inner')
    assert len(_nodes(root, 'GeometryNodeGroup')) == 1
    assert len(_nodes(_nodes(root, 'GeometryNodeGroup')[0].node_tree, 'ShaderNodeMath')) == 1


def test_nested_inline_inside_inline_never_constructs_helper_group():
    """Two nested source bodies are lowered to the exact same caller tree."""
    root = _compose('''def inner(value: Float, __inline__=True) -> Float:
    return value + 1.0

def outer(value: Float, __inline__=True) -> Float:
    return inner(value) * 2.0
result = outer(2.0)
''', 'NFTest_Inline_Inside_Inline')
    assert not _nodes(root, 'GeometryNodeGroup')
    assert len(_nodes(root, 'ShaderNodeMath')) == 2


def test_nested_unique_inside_two_inline_occurrences_produces_two_backing_groups():
    """An inline owner's scope participates in its nested UNIQUE instance key."""
    root = _compose('''def inner(value: Float) -> Float:
    return value + 1.0

def outer(value: Float, __inline__=True) -> Float:
    return inner(value, __unique__=True)
first = outer(2.0)
second = outer(3.0)
''', 'NFTest_Inline_Nested_Unique')
    nodes = _nodes(root, 'GeometryNodeGroup')
    assert len(nodes) == 2
    assert nodes[0].node_tree.as_pointer() != nodes[1].node_tree.as_pointer()


def test_inline_repeat_if_and_grid_uv_use_caller_tree_without_context_leaks():
    """If/Repeat and contextual values lower inside the active physical tree."""
    root = _compose('''def compute(value: Float, flag: Bool, __inline__=True) -> Float:
    for i in repeat_range(2):
        if flag:
            value = value + 1.0
        else:
            value = value + 2.0
    return value

def make_uv(size: Float, __inline__=True) -> Vector:
    geo = grid(size, 3.0)
    return grid_uv()
result = compute(1.0, True)
first_uv = make_uv(2.0)
second_uv = make_uv(3.0)
''', 'NFTest_Inline_Context_If_Repeat')
    assert not _nodes(root, 'GeometryNodeGroup')
    assert len(_nodes(root, 'GeometryNodeRepeatInput')) == 1
    assert len(_nodes(root, 'GeometryNodeRepeatOutput')) == 1
    assert len(_nodes(root, 'GeometryNodeSwitch')) >= 1
    assert len(_nodes(root, 'GeometryNodeMeshGrid')) == 2
    assert all(link.is_valid for link in root.links)


def test_two_inline_object_users_have_private_info_nodes():
    """Object.info caches belong to each inline invocation, not the source socket."""
    outer = compile_entry_group('''def read_location(obj: Object, __inline__=True) -> Vector:
    obj.info(as_instance=False)
    return obj.location

def grouped_parent(obj: Object) -> Vector:
    first = read_location(obj)
    second = read_location(obj)
    return first + second
''', 'NFTest_Inline_Object_Info', 'grouped_parent')
    assert len(_nodes(outer, 'GeometryNodeObjectInfo')) == 2
    assert not _nodes(outer, 'GeometryNodeGroup')
    assert all(node.inputs['As Instance'].default_value is False
               for node in _nodes(outer, 'GeometryNodeObjectInfo'))


def test_selected_group_reload_tracks_inline_source_default_and_rolls_back():
    """An inline dependency rebuilds its real grouped parent without a helper group."""
    from NodeForge.compiler_identities import parse_module_definition_function_id
    from NodeForge.function_instances import stored_fingerprint

    source = '''def inline_leaf(value: Float, offset: Float = 2.0, __inline__=True) -> Float:
    return value + offset

def grouped_parent(value: Float) -> Float:
    return inline_leaf(value)
result = grouped_parent(3.0)
'''
    root = _compose(source, 'NFTest_Inline_Parent_Reload')
    parent, = [node.node_tree for node in _nodes(root, 'GeometryNodeGroup')]
    saved_pointer = parent.as_pointer()
    saved_fingerprint = stored_fingerprint(parent)
    assert saved_fingerprint
    assert len(_nodes(parent, 'ShaderNodeMath')) == 1
    assert not _nodes(parent, 'GeometryNodeGroup')
    origin = parse_module_definition_function_id(parent['nodeforge_module_definition']).module.origin

    # Changing only the inline callee's default invalidates this grouped body.
    changed = source.replace('offset: Float = 2.0', 'offset: Float = 4.0')
    compiler.update_library_catalog_group(parent, source=changed, module_context=origin)
    new_fingerprint = stored_fingerprint(parent)
    assert parent.as_pointer() == saved_pointer
    assert new_fingerprint != saved_fingerprint
    assert len(_nodes(parent, 'ShaderNodeMath')) == 1
    assert not _nodes(parent, 'GeometryNodeGroup')

    compiler.update_library_catalog_group(parent, source=changed, module_context=origin)
    assert stored_fingerprint(parent) == new_fingerprint
    assert parent.as_pointer() == saved_pointer

    # A changed nested definition is rejected before publishing candidate nodes.
    broken = changed.replace('value + offset', 'value + missing_variable')
    previous_nodes = tuple((node.bl_idname, node.name) for node in parent.nodes)
    with pytest.raises(CompileError):
        compiler.update_library_catalog_group(parent, source=broken, module_context=origin)
    assert stored_fingerprint(parent) == new_fingerprint
    assert parent.as_pointer() == saved_pointer
    assert tuple((node.bl_idname, node.name) for node in parent.nodes) == previous_nodes


def test_direct_root_update_replaces_inline_expansion_and_staging_failure_rolls_back(monkeypatch):
    """Root Update owns inline node replacement and its existing rollback boundary."""
    from NodeForge.blender import ir_lowering

    tree = bpy.data.node_groups.new('NFTest_Inline_Root_Update', 'GeometryNodeTree')
    compiler.update_source_in_tree('a = cube(1.0)\n', tree)
    before = {node.as_pointer() for node in tree.nodes}
    source = '''def inline_leaf(value: Float, __inline__=True) -> Float:
    return value + 1.0
result = inline_leaf(index())
'''
    compiler.update_source_in_tree(source, tree)
    assert len(_nodes(tree, 'ShaderNodeMath')) == 2  # Int-to-Float adapter and inline addition.
    assert not _nodes(tree, 'GeometryNodeGroup')
    assert not before.intersection(node.as_pointer() for node in tree.nodes)
    pointers = {node.as_pointer() for node in tree.nodes}
    group_pointers = {g.as_pointer() for g in bpy.data.node_groups}
    original = ir_lowering._math

    def injected(*args, **kwargs):
        """Fail after a native Math node is staged inside an inline expansion."""
        original(*args, **kwargs)
        raise RuntimeError('injected inline lowering failure')

    monkeypatch.setattr(ir_lowering, '_math', injected)
    from NodeForge.blender.root_update import RootUpdateError
    with pytest.raises(RootUpdateError, match='injected inline lowering failure'):
        compiler.update_source_in_tree(source.replace('1.0', '2.0'), tree)
    assert pointers == {node.as_pointer() for node in tree.nodes}
    assert group_pointers == {g.as_pointer() for g in bpy.data.node_groups}


@pytest.mark.parametrize('marker', ('__inline__=True', '__inline__: Bool=True'))
def test_standalone_and_declaration_only_materialization_ignore_compiler_parameter(marker):
    """Both syntax forms keep one runtime input and one editable real group."""
    from NodeForge.function_instances import FUNCTION_DEFINITION_OWNER_PROP

    source = (f'def inline_standalone(value: Float, {marker}) -> Float:\n'
              '    return value + 2.0\n')
    standalone = compiler.create_expression_group(source, 'NFTest_Inline_Standalone')
    assert len([item for item in standalone.interface.items_tree
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT']) == 1
    assert not any('__inline__' in item.name for item in standalone.interface.items_tree)
    assert standalone.get(FUNCTION_DEFINITION_OWNER_PROP)
    assert len(_nodes(standalone, 'ShaderNodeMath')) == 1

    root = _compose(source, 'NFTest_Inline_Definition_Only')
    group_node, = _nodes(root, 'GeometryNodeGroup')
    assert group_node.node_tree.get('nodeforge_module_name') == 'inline_standalone'
    assert len(group_node.inputs) == 1
    assert len(_nodes(group_node.node_tree, 'ShaderNodeMath')) == 1


@pytest.mark.parametrize('marker', ('__inline__=True', '__inline__: Bool=True'))
def test_selected_reload_pref_only_edit_retains_group_and_external_links(marker):
    """Compiler-only policy cannot change persistent signatures or detach group users."""
    from NodeForge.compiler_identities import parse_module_definition_function_id
    from NodeForge.function_instances import (
        FUNCTION_DEFINITION_OWNER_PROP, FUNCTION_INSTANCE_KEY_PROP,
    )

    initial = (f'def inline_editable(value: Float, {marker}) -> Float:\n'
               '    return value + 1.0\n')
    group = compiler.create_expression_group(initial, 'NFTest_Inline_Editable')
    external = bpy.data.node_groups.new('NFTest_Inline_External_User', 'GeometryNodeTree')
    reference = external.nodes.new('GeometryNodeGroup')
    reference.node_tree = group
    group_ptr = group.as_pointer()
    identity = group[FUNCTION_DEFINITION_OWNER_PROP]
    instance = group.get(FUNCTION_INSTANCE_KEY_PROP)
    signature = parse_module_definition_function_id(group['nodeforge_module_definition']).signature
    origin = parse_module_definition_function_id(group['nodeforge_module_definition']).module.origin
    assert '__inline__' not in signature

    changed = initial.replace('True', 'False')
    assert compiler.update_library_catalog_group(group, source=changed, module_context=origin) is group
    assert group.as_pointer() == group_ptr
    assert group[FUNCTION_DEFINITION_OWNER_PROP] == identity
    assert group.get(FUNCTION_INSTANCE_KEY_PROP) == instance
    assert reference.node_tree is group and len(reference.inputs) == 1
    assert all(link.is_valid for link in group.links)

    incompatible = changed.replace('value: Float', 'different: Float').replace(
        'return value', 'return different')
    with pytest.raises(CompileError, match='signature is incompatible|types are incompatible'):
        compiler.update_library_catalog_group(group, source=incompatible, module_context=origin)
    assert reference.node_tree is group
    assert group.as_pointer() == group_ptr
    assert group[FUNCTION_DEFINITION_OWNER_PROP] == identity


@pytest.mark.parametrize('marker', ('__inline__=True', '__inline__: Bool=True'))
def test_mesh_compute_typed_entry_excludes_compiler_only_parameter(marker):
    """Explicit root Mesh Compute compiles runtime sockets from the canonical signature."""
    source = (f'def mesh_entry(geometry: Geometry, strength: Float=1.0, {marker}) -> Geometry:\n'
              '    return geometry\n')
    backend = compiler._new_group_backend()
    prepared, _session = compiler._prepare_mesh_compute_root(backend, source, name='mesh_entry')
    assert tuple(item.source_name for item in prepared.interface.inputs) == ('geometry', 'strength')
    assert '__inline__' not in repr(prepared.interface)


def test_inline_and_grouped_float_outputs_evaluate_equally():
    """Both materialization policies produce the same numeric Blender field result."""
    from test_module_compile_time_fingerprints import _evaluate_float_output

    for label, modifier in (('inline', ''), ('grouped', ', __inline__=False')):
        source = '''def add_two(value: Float, __inline__=True) -> Float:
    return value + 2.0
result = add_two(3.0%s)
''' % modifier
        tree = _compose(source, f'NFTest_Inline_Evaluate_{label}')
        if label == 'inline':
            assert not _nodes(tree, 'GeometryNodeGroup')
            socket = _nodes(tree, 'ShaderNodeMath')[-1].outputs[0]
        else:
            grouped, = _nodes(tree, 'GeometryNodeGroup')
            socket = grouped.outputs[0]
        assert _evaluate_float_output(tree, socket) == pytest.approx(5.0, abs=1e-5)


def test_inline_color_alpha_survives_evaluated_field_output():
    """The native Color constant retains its alpha after actual Geometry Nodes evaluation."""
    from test_module_compile_time_fingerprints import _evaluate_float_output

    tree = _compose('''def rgba(value: Color=(0.1, 0.2, 0.3, 0.4), __inline__=True) -> Color:
    return value
result = rgba()
''', 'NFTest_Inline_Color_Alpha')
    color, = _nodes(tree, 'FunctionNodeInputColor')
    separate = tree.nodes.new('FunctionNodeSeparateColor')
    tree.links.new(color.outputs[0], separate.inputs['Color'])
    assert _evaluate_float_output(tree, separate.outputs['Alpha']) == pytest.approx(0.4, abs=1e-5)


def test_nested_package_inline_identity_and_source_add(tmp_path):
    """Qualified same-name package exports inline independently and still support Add."""
    import json
    from NodeForge import packages

    packages.set_packages_dir_for_tests(tmp_path / 'installed')
    try:
        source_root = tmp_path / 'source'
        for component, amount in (('mesh', 2), ('curve', 3)):
            directory = source_root / 'functions' / component
            directory.mkdir(parents=True)
            (directory / 'ops.nf').write_text(
                'def shared(value: Float, __inline__=True) -> Float:\n'
                f'    return value + {amount}.0\n', encoding='utf8',
            )
        (source_root / 'nodeforge_package.json').write_text(json.dumps({
            'schema_version': 1, 'id': 'vendor.inline_regression', 'import_name': 'inlinepkg',
            'name': 'Inline regression', 'version': '1.0.0', 'author': 'Tests',
            'nodeforge_min_version': '0.65.0', 'contents': {'functions': 'functions'},
        }), encoding='utf8')
        packages.install_package_directory(source_root)
        tree = _compose('''from packages import inlinepkg
x = inlinepkg.mesh.ops.shared(1.0)
y = inlinepkg.curve.ops.shared(1.0)
''', 'NFTest_Inline_Nested_Namespaces')
        assert not _nodes(tree, 'GeometryNodeGroup')
        assert len(_nodes(tree, 'ShaderNodeMath')) == 2
        a = compiler.create_package_function_group('vendor.inline_regression', 'mesh.ops.shared')
        b = compiler.create_package_function_group('vendor.inline_regression', 'curve.ops.shared')
        assert a is not b
        assert a['nodeforge_module_definition'] != b['nodeforge_module_definition']
        assert len(_nodes(a, 'ShaderNodeMath')) == 1
        assert len(_nodes(b, 'ShaderNodeMath')) == 1
    finally:
        packages.set_packages_dir_for_tests(None)


def test_selected_inline_parent_preserves_owner_across_save_reopen(tmp_path):
    """Source-backed parent groups containing inlined nodes remain Reloadable in .blend."""
    source = '''def inc(value: Float, __inline__=True) -> Float:
    return value + 1.0

def wrapper(value: Float) -> Float:
    return inc(value)
result = wrapper(2.0)
'''
    root = _compose(source, 'NFTest_Inline_Save_Reopen')
    group, = [node.node_tree for node in _nodes(root, 'GeometryNodeGroup')]
    saved_owner = group['nodeforge_module_definition']
    group_name = group.name
    from NodeForge.compiler_identities import parse_module_definition_function_id
    origin = parse_module_definition_function_id(saved_owner).module.origin
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp_path / 'inline_reload.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp_path / 'inline_reload.blend'))
    selected = bpy.data.node_groups[group_name]
    selected_ptr = selected.as_pointer()
    assert selected['nodeforge_module_definition'] == saved_owner
    assert compiler.update_library_catalog_group(selected, source=source, module_context=origin) is selected
    assert selected.as_pointer() == selected_ptr
    assert len(_nodes(selected, 'ShaderNodeMath')) == 1
    assert not _nodes(selected, 'GeometryNodeGroup')


def test_selected_group_mid_inline_backend_failure_preserves_published_nodes(monkeypatch):
    """Physical parent Reload rolls back a fault after staging native inline nodes."""
    from NodeForge.compiler_identities import parse_module_definition_function_id
    from NodeForge.function_instances import stored_fingerprint
    from NodeForge.blender import ir_lowering

    source = '''def inner(value: Float, __inline__=True) -> Float:
    return value + 1.0

def outer(value: Float) -> Float:
    return inner(value)
result = outer(2.0)
'''
    base = _compose(source, 'NFTest_Inline_Group_Rollback')
    target, = [node.node_tree for node in _nodes(base, 'GeometryNodeGroup')]
    origin = parse_module_definition_function_id(target['nodeforge_module_definition']).module.origin
    ptr = target.as_pointer()
    before = stored_fingerprint(target)
    node_pointers = {node.as_pointer() for node in target.nodes}
    group_pointers = {group.as_pointer() for group in bpy.data.node_groups}
    original = ir_lowering._math

    def injected(*args, **kwargs):
        """Raise after the inline Math node exists on the replacement candidate."""
        original(*args, **kwargs)
        raise RuntimeError('injected grouped inline failure')

    monkeypatch.setattr(ir_lowering, '_math', injected)
    with pytest.raises(RuntimeError, match='injected grouped inline failure'):
        compiler.update_library_catalog_group(
            target, source=source.replace('1.0', '4.0'), module_context=origin,
        )
    assert target.as_pointer() == ptr
    assert stored_fingerprint(target) == before
    assert {node.as_pointer() for node in target.nodes} == node_pointers
    assert {group.as_pointer() for group in bpy.data.node_groups} == group_pointers
    assert all(link.is_valid for link in target.links)
