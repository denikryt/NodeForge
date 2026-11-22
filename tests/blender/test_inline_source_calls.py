"""Physical Blender 5.2 regressions for .nf occurrence-scoped inline compilation."""

from __future__ import annotations

import pytest

from helpers import *

pytestmark = pytest.mark.blender


def _compose(source, title):
    """Compile a source module into a fresh Geometry Nodes base tree."""
    tree = bpy.data.node_groups.new(title, 'GeometryNodeTree')
    compile_root(source, tree)
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


def test_inline_rotation_default_links_native_rotation_socket():
    """An omitted Rotation default creates one native Input Rotation in the caller."""
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    tree = _compose('''def apply_rotation(geometry: Geometry,
                   rotation: Rotation = (0.0, 0.0, 1.5707963),
                   __inline__=True) -> Geometry:
    return node('GeometryNodeTransform',
                inputs={ID('Geometry'): geometry, ID('Rotation'): rotation},
                output=ID('Geometry'), typ=Geometry)
result = apply_rotation(cube(1.0))
''', 'NFTest_Inline_Rotation_Default')
    constants = _nodes(tree, 'FunctionNodeInputRotation')
    transforms = _nodes(tree, 'GeometryNodeTransform')
    assert len(constants) == 0 and len(transforms) == 1
    assert not _nodes(tree, 'GeometryNodeGroup')
    assert not transforms[0].inputs['Rotation'].is_linked
    assert tuple(transforms[0].inputs['Rotation'].default_value) == tuple(
        normalize_float_constant(v) for v in (0.0, 0.0, 1.5707963)
    )
    assert all(link.is_valid for link in tree.links)


def test_inline_rotation_return_reuse_and_separate_occurrences():
    """One invocation shares its default node; a second invocation owns another."""
    returned = _compose('''def identity(rotation: Rotation = (-0.25, 0.5, 8.0),
                 __inline__=True) -> Rotation:
    return rotation
result = identity()
''', 'NFTest_Inline_Rotation_Return')
    assert len(_nodes(returned, 'FunctionNodeInputRotation')) == 1
    assert not _nodes(returned, 'GeometryNodeGroup')

    tree = _compose('''def twice(geometry: Geometry, rotation: Rotation = (0, 0, 1.0),
          __inline__=True) -> Geometry:
    first = node('GeometryNodeTransform',
                 inputs={ID('Geometry'): geometry, ID('Rotation'): rotation},
                 output=ID('Geometry'), typ=Geometry)
    return node('GeometryNodeTransform',
                inputs={ID('Geometry'): first, ID('Rotation'): rotation},
                output=ID('Geometry'), typ=Geometry)
one = twice(cube(1.0))
result = twice(one)
''', 'NFTest_Inline_Rotation_Reuse')
    constants = _nodes(tree, 'FunctionNodeInputRotation')
    transforms = _nodes(tree, 'GeometryNodeTransform')
    assert len(constants) == 2
    assert len(transforms) == 4
    assert not _nodes(tree, 'GeometryNodeGroup')
    for rotation in constants:
        consumers = [link for link in tree.links if link.from_socket == rotation.outputs['Rotation']]
        assert len(consumers) == 2
        assert all(link.to_socket.name == 'Rotation' for link in consumers)
    assert all(link.is_valid for link in tree.links)


def test_inline_rotation_nested_and_grouped_caller():
    """Nested inlines use the immediate physical owner, including grouped parents."""
    source = '''def rotation_constant(rotation: Rotation = (0, -0.1, 7.0),
                      __inline__=True) -> Rotation:
    return rotation

def rotate_inline(geometry: Geometry, __inline__=True) -> Geometry:
    return node('GeometryNodeTransform',
                inputs={ID('Geometry'): geometry, ID('Rotation'): rotation_constant()},
                output=ID('Geometry'), typ=Geometry)

def rotate_grouped(geometry: Geometry) -> Geometry:
    return rotate_inline(geometry)

result = rotate_grouped(cube(1.0))
'''
    root = _compose(source, 'NFTest_Inline_Rotation_Nested')
    grouped, = _nodes(root, 'GeometryNodeGroup')
    nested_group = grouped.node_tree
    assert not _nodes(nested_group, 'GeometryNodeGroup')
    assert len(_nodes(nested_group, 'FunctionNodeInputRotation')) == 1
    assert len(_nodes(nested_group, 'GeometryNodeTransform')) == 1
    assert all(link.is_valid for link in nested_group.links)


def test_inline_rotation_explicit_runtime_and_grouped_default():
    """Typed runtime Rotation creates no constant; grouped calls keep socket defaults."""
    tree = _compose('''def apply_rotation(geometry: Geometry,
                   rotation: Rotation = (0, 0, 0.5), __inline__=True) -> Geometry:
    return node('GeometryNodeTransform',
                inputs={ID('Geometry'): geometry, ID('Rotation'): rotation},
                output=ID('Geometry'), typ=Geometry)
euler = vector(0.0, 0.0, 1.0)
runtime_rotation = node('FunctionNodeEulerToRotation',
                        inputs={ID('Euler'): euler},
                        output=ID('Rotation'), typ=Rotation)
one = apply_rotation(cube(1.0), runtime_rotation)
result = apply_rotation(one, __inline__=False)
''', 'NFTest_Inline_Rotation_Runtime')
    assert not _nodes(tree, 'FunctionNodeInputRotation')
    transforms = _nodes(tree, 'GeometryNodeTransform')
    producer, = _nodes(tree, 'FunctionNodeEulerToRotation')
    assert len(transforms) == 1
    assert transforms[0].inputs['Rotation'].links[0].from_socket == producer.outputs['Rotation']
    grouped, = _nodes(tree, 'GeometryNodeGroup')
    rotation_input, = [socket for socket in grouped.inputs if socket.bl_idname == 'NodeSocketRotation']
    assert tuple(rotation_input.default_value) == pytest.approx((0, 0, 0.5), abs=1e-7)
    assert not _nodes(grouped.node_tree, 'FunctionNodeInputRotation')
    assert all(link.is_valid for link in tree.links)


def test_inline_rotation_default_invalid_or_vector_runtime_rejected():
    """Inline lowering retains the semantic Rotation default and type contract."""
    from NodeForge.errors import CompileError

    for bad in ('(0, 1)', '(0, True, 1)', '(0, 1e50, 1)'):
        tree = bpy.data.node_groups.new('NFTest_Inline_Bad_Rotation', 'GeometryNodeTree')
        with pytest.raises(CompileError):
            compile_root(
                'def identity(rotation: Rotation = ' + bad + ', __inline__=True) -> Rotation:\n'
                '    return rotation\nresult = identity()\n', tree,
            )
    wrong = bpy.data.node_groups.new('NFTest_Inline_Bad_Runtime', 'GeometryNodeTree')
    with pytest.raises(CompileError):
        compile_root('''def identity(rotation: Rotation, __inline__=True) -> Rotation:
    return rotation
result = identity(vector(0, 0, 0))
''', wrong)


def test_inline_rotation_evaluates_like_grouped_function():
    """Constant Rotation input produces the same evaluated mesh in either mode."""
    from test_update_group import _evaluate_group_geometry_vertices

    source = '''def apply_rotation(geometry: Geometry,
                   rotation: Rotation = (0.2, -0.3, 1.1),
                   __inline__=True) -> Geometry:
    return node('GeometryNodeTransform',
                inputs={ID('Geometry'): geometry, ID('Rotation'): rotation},
                output=ID('Geometry'), typ=Geometry)
result = apply_rotation(cube(1.0)%s)
'''
    evaluated = {}
    for mode, override in (('inline', ''), ('grouped', ', __inline__=False')):
        tree = _compose(source % override, f'NFTest_Inline_Rotation_Evaluate_{mode}')
        result_socket = (_nodes(tree, 'GeometryNodeTransform')[0].outputs['Geometry']
                         if mode == 'inline' else _nodes(tree, 'GeometryNodeGroup')[0].outputs[0])
        tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        output_node = tree.nodes.new('NodeGroupOutput')
        tree.links.new(result_socket, output_node.inputs['Geometry'])
        evaluated[mode] = _evaluate_group_geometry_vertices(tree, f'NFTest_Inline_Rotation_{mode}')

    assert len(evaluated['inline']) == len(evaluated['grouped']) == 8
    for inline_coord, grouped_coord in zip(evaluated['inline'], evaluated['grouped']):
        assert inline_coord == pytest.approx(grouped_coord, abs=1e-5)


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
    compile_root('a = cube(1.0)\n', tree)
    before = {node.as_pointer() for node in tree.nodes}
    source = '''def inline_leaf(value: Float, __inline__=True) -> Float:
    return value + 1.0
result = inline_leaf(index())
'''
    compile_root(source, tree)
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
        compile_root(source.replace('1.0', '2.0'), tree)
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
    assert _nodes(root, 'GeometryNodeGroup') == []
    group = published_entry(root, 'inline_standalone')
    assert len([item for item in group.interface.items_tree
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT']) == 1
    assert len(_nodes(group, 'ShaderNodeMath')) == 1


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


def test_inline_rotation_package_function_uses_caller_tree(tmp_path):
    """Imported Rotation defaults materialize in the immediate caller's tree."""
    import json
    from NodeForge import packages

    packages.set_packages_dir_for_tests(tmp_path / 'installed')
    try:
        source_root = tmp_path / 'source'
        directory = source_root / 'functions' / 'spatial'
        directory.mkdir(parents=True)
        (directory / 'rotate.nf').write_text('''def apply(geometry: Geometry,
          rotation: Rotation = (0.1, -0.2, 7.0), __inline__=True) -> Geometry:
    return node('GeometryNodeTransform',
                inputs={ID('Geometry'): geometry, ID('Rotation'): rotation},
                output=ID('Geometry'), typ=Geometry)
''', encoding='utf8')
        (source_root / 'nodeforge_package.json').write_text(json.dumps({
            'schema_version': 1, 'id': 'vendor.rotation_inline',
            'import_name': 'rotationpkg', 'name': 'Rotation inline regression',
            'version': '1.0.0', 'author': 'Tests',
            'nodeforge_min_version': '0.65.0', 'contents': {'functions': 'functions'},
        }), encoding='utf8')
        packages.install_package_directory(source_root)
        tree = _compose('''from packages import rotationpkg
result = rotationpkg.spatial.rotate.apply(cube(1.0))
''', 'NFTest_Inline_Rotation_Package')
        assert not _nodes(tree, 'FunctionNodeInputRotation')
        transform, = _nodes(tree, 'GeometryNodeTransform')
        assert not transform.inputs['Rotation'].is_linked
        assert tuple(transform.inputs['Rotation'].default_value) == pytest.approx((0.1, -0.2, 7.0), abs=1e-6)
        assert not _nodes(tree, 'GeometryNodeGroup')
        assert all(link.is_valid for link in tree.links)
    finally:
        packages.set_packages_dir_for_tests(None)


def test_grouped_parent_reload_updates_inline_rotation_and_rolls_back(monkeypatch):
    """Reload tracks inline Rotation defaults and retains the published group on failure."""
    from NodeForge.compiler_identities import parse_module_definition_function_id
    from NodeForge.blender import nodes as blender_nodes

    source = '''def inner(rotation: Rotation = (0, 0, 0.25), __inline__=True) -> Rotation:
    return rotation

def outer(geometry: Geometry) -> Geometry:
    return node('GeometryNodeTransform',
                inputs={ID('Geometry'): geometry, ID('Rotation'): inner()},
                output=ID('Geometry'), typ=Geometry)

result = outer(cube(1.0))
'''
    root = _compose(source, 'NFTest_Inline_Rotation_Reload')
    grouped, = _nodes(root, 'GeometryNodeGroup')
    parent = grouped.node_tree
    origin = parse_module_definition_function_id(parent['nodeforge_module_definition']).module.origin
    pointer = parent.as_pointer()
    initial, = _nodes(parent, 'FunctionNodeInputRotation')
    assert initial.rotation_euler.z == pytest.approx(0.25)

    updated_source = source.replace('0.25', '0.75')
    assert compiler.update_library_catalog_group(
        parent, source=updated_source, module_context=origin,
    ) is parent
    assert parent.as_pointer() == pointer
    updated, = _nodes(parent, 'FunctionNodeInputRotation')
    assert updated.rotation_euler.z == pytest.approx(0.75)
    before_nodes = {node.as_pointer() for node in parent.nodes}
    before_groups = {group.as_pointer() for group in bpy.data.node_groups}
    original = blender_nodes._rotation_value

    def fail_after_native_rotation(*args, **kwargs):
        """Simulate failure after creating the staged Rotation node."""
        original(*args, **kwargs)
        raise RuntimeError('injected inline Rotation failure')

    monkeypatch.setattr(blender_nodes, '_rotation_value', fail_after_native_rotation)
    with pytest.raises(RuntimeError, match='injected inline Rotation failure'):
        compiler.update_library_catalog_group(
            parent, source=source.replace('0.25', '1.25'), module_context=origin,
        )
    assert parent.as_pointer() == pointer
    assert {node.as_pointer() for node in parent.nodes} == before_nodes
    assert {group.as_pointer() for group in bpy.data.node_groups} == before_groups
    assert _nodes(parent, 'FunctionNodeInputRotation')[0].rotation_euler.z == pytest.approx(0.75)
    assert all(link.is_valid for link in parent.links)


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


def test_installed_nodeforge_nodes_constants_respect_socket_fallbacks(tmp_path):
    """Real v1.3 wrapper defaults preserve ordinary and contextual socket semantics."""
    from pathlib import Path
    from NodeForge import packages

    source_root = Path(__file__).resolve().parents[3] / 'nodeforge.nodes'
    if not (source_root / 'nodeforge_package.json').exists():
        pytest.skip('nodeforge.nodes integration source is not installed beside NodeForge')
    packages.set_packages_dir_for_tests(tmp_path / 'installed')
    try:
        packages.install_package_directory(source_root)
        cube_tree = _compose('''from packages import nodes
result, uv = nodes.mesh.primitives.mesh_cube(size_value=vector(2, 1, 1.5))
''', 'NFTest_Nodes_Issue67_Cube')
        native_cube, = _nodes(cube_tree, 'GeometryNodeMeshCube')
        assert not native_cube.inputs['Size'].is_linked
        assert tuple(native_cube.inputs['Size'].default_value) == pytest.approx((2, 1, 1.5))
        assert not _nodes(cube_tree, 'ShaderNodeCombineXYZ')
        assert not _nodes(cube_tree, 'GeometryNodeGroup')

        write_tree = _compose('''from packages import nodes
result = nodes.geometry.write.set_position_node(cube(1.0))
''', 'NFTest_Nodes_Issue67_Position')
        node, = _nodes(write_tree, 'GeometryNodeSetPosition')
        assert node.inputs['Position'].is_linked
        assert node.inputs['Position'].links[0].from_socket.bl_idname == 'NodeSocketVector'
        assert not _nodes(write_tree, 'GeometryNodeGroup')

        grouped_tree = _compose('''from packages import nodes
result, uv = nodes.mesh.primitives.mesh_cube(__inline__=False)
''', 'NFTest_Nodes_Issue67_Grouped')
        group_node, = _nodes(grouped_tree, 'GeometryNodeGroup')
        assert not _nodes(grouped_tree, 'GeometryNodeMeshCube')
        assert len(_nodes(group_node.node_tree, 'GeometryNodeMeshCube')) == 1
    finally:
        packages.set_packages_dir_for_tests(None)
