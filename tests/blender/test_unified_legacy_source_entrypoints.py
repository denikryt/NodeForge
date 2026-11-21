"""Legacy source syntax must never publish through compiler or backend entry points."""

from helpers import *


_OLD = 'radius = input_float("Radius", default=1.0)\noutput("Result", radius)'
_NEW = 'def radius(value: Float = 1.0) -> Float:\n    return value + 1\n'


def test_legacy_root_interface_is_rejected_before_any_blender_mutation():
    tree = bpy.data.node_groups.new('NFTest_legacy_denial', 'GeometryNodeTree')
    node = tree.nodes.new('GeometryNodeMeshCube')
    before = tuple(x.as_pointer() for x in tree.nodes)
    existing = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='legacy .nf interface'):
        compile_root(_OLD, tree)
    with pytest.raises(CompileError, match='legacy .nf interface'):
        compiler.create_expression_group(_OLD)
    with pytest.raises(CompileError, match='legacy .nf interface'):
        compiler.compile_script_auto(_OLD, tree)
    with pytest.raises(CompileError, match='canonical MODULE_DEF'):
        compiler.update_expression_group(tree, _OLD)
    assert before == tuple(x.as_pointer() for x in tree.nodes)
    assert node in tree.nodes.values()
    assert {group.as_pointer() for group in bpy.data.node_groups} == existing


def test_unified_single_def_uses_prepared_backend_in_place():
    group = compiler.create_expression_group(_NEW, 'NFTest_unified_single_def')
    pointer = group.as_pointer()
    assert group.get('nodeforge_module_definition')
    assert compiler.update_expression_group(group, _NEW.replace('value + 1', 'value + 6')) is group
    assert group.as_pointer() == pointer
    assert 'value + 6' in group['nodeforge_library_source']


def test_compiler_facade_requires_explicit_base_target_and_no_generated_root():
    with pytest.raises(CompileError, match='existing base GeometryNodeTree'):
        compiler.compile_script_auto('x = cube(1)')
    target = bpy.data.node_groups.new('NFTest_unified_facade_target', 'GeometryNodeTree')
    assert compiler.compile_script_auto('x = cube(1)', target) == (target, False)
    assert any(node.bl_idname == 'GeometryNodeMeshCube' for node in target.nodes)


def test_backend_raw_source_routes_cannot_bypass_legacy_prohibition():
    backend = compiler._new_group_backend()
    identity = backend.resolve_root_compilation_identity()
    with pytest.raises(CompileError, match='Raw-source backend preparation'):
        backend.prepare_source_compilation(_OLD, compilation_identity=identity)
    with pytest.raises(CompileError, match='Raw-source backend compilation'):
        backend.compile_group_callback(_OLD)
    with pytest.raises(CompileError, match='legacy .nf interface'):
        compiler._prepare_mesh_compute_root(backend, _OLD, name='Blocked')
