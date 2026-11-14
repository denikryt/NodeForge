"""Module-defined functions use the established nested Blender backend."""

from helpers import *
from NodeForge.function_instances import FUNCTION_INSTANCE_KEY_PROP


def _module_groups(name):
    return [group for group in bpy.data.node_groups
            if group.get("nodeforge_module_name") == name]


def _nested_groups(tree):
    return [node for node in tree.nodes if node.bl_idname == "GeometryNodeGroup"]


def _compose_module(source, name, *, environment=None):
    """Compile unified top-level statements into a pre-existing base node tree."""
    root = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    compiler.compose_source_initial(source, root, resolved_environment=environment)
    return root


def test_module_definition_is_physical_nested_group():
    root = _compose_module('''def module_leaf(x: Float) -> Float:
    return x + 2

value = module_leaf(3.0)
value = value
''', 'NFTest_module_definition_physical')
    leaf = _module_groups('module_leaf')
    assert len(leaf) == 1
    assert len(_nested_groups(root)) == 1
    assert _nested_groups(root)[0].node_tree is leaf[0]
    assert any(n.bl_idname == 'ShaderNodeMath' for n in leaf[0].nodes)


def test_nested_module_functions_materialize_through_shared_backend():
    root = _compose_module('''def nested_module_leaf(x: Float) -> Float:
    return x + 1

def nested_module_wrapper(x: Float) -> Float:
    return nested_module_leaf(x)

result = nested_module_wrapper(5)
result = result
''', 'NFTest_nested_module_function_physical')
    wrapper = _module_groups('nested_module_wrapper')
    leaf = _module_groups('nested_module_leaf')
    assert len(wrapper) == len(leaf) == 1
    assert _nested_groups(root)[0].node_tree is wrapper[0]
    assert _nested_groups(wrapper[0])[0].node_tree is leaf[0]


def test_module_calls_share_by_default_and_split_unique_instances():
    root = _compose_module('''def module_shared_unique_leaf(x: Float) -> Float:
    return x + 1

a = module_shared_unique_leaf(1)
b = module_shared_unique_leaf(2)
c = module_shared_unique_leaf(3, __unique__=True)
total = a + b + c
''', 'NFTest_module_shared_unique')
    leaves = _module_groups('module_shared_unique_leaf')
    assert len(leaves) == 2
    nodes = _nested_groups(root)
    assert len(nodes) == 3
    assert nodes[0].node_tree is nodes[1].node_tree
    assert nodes[2].node_tree is not nodes[0].node_tree
    assert len([leaf for leaf in leaves if leaf.get(FUNCTION_INSTANCE_KEY_PROP)]) == 1


def test_module_function_in_place_update_retains_datablock_pointer():
    before = '''def update_module_leaf(x: Float) -> Float:
    return x + 2

value = update_module_leaf(3)
value = value
'''
    after = before.replace('x + 2', 'x + 4')
    root = _compose_module(before, 'NFTest_module_in_place_update')
    group = _module_groups('update_module_leaf')[0]
    pointer = group.as_pointer()
    from NodeForge.compiler_identities import parse_module_definition_function_id
    origin = parse_module_definition_function_id(group['nodeforge_module_definition']).module.origin
    compiler.update_library_catalog_group(group, source=after, module_context=origin)
    updated = _module_groups('update_module_leaf')
    assert len(updated) == 1
    assert updated[0].as_pointer() == pointer
    assert any(node.bl_idname == 'ShaderNodeMath' for node in updated[0].nodes)


def test_imported_package_module_definition_is_nested_and_import_has_no_effect(tmp_path):
    import json
    from NodeForge import packages
    root = tmp_path / 'package_source'
    source_dir = root / 'functions'
    source_dir.mkdir(parents=True)
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.modulefixture', 'import_name': 'modulefixture',
        'name': 'Module fixture', 'version': '1.0.0', 'author': 'Unit fixture',
        'description': 'Nested module member import', 'contents': {'functions': 'functions'},
        'nodeforge_min_version': '0.65.0', 'nodeforge_max_version': None,
        'permissions': {'python': False},
    }), encoding='utf-8')
    (source_dir / 'geometry.nf').write_text('''def package_leaf(x: Float) -> Float:
    return x + 1

def package_wrapper(x: Float) -> Float:
    return package_leaf(x)

cube(1)
''', encoding='utf-8')
    packages.install_package_directory(root)
    root_tree = _compose_module('''from packages import modulefixture
v = modulefixture.package_wrapper(3)
v = v
''', 'NFTest_package_module_materialized')
    wrapper = _module_groups('package_wrapper')
    leaf = _module_groups('package_leaf')
    assert len(wrapper) == len(leaf) == 1
    assert len(_nested_groups(root_tree)) == 1
    assert _nested_groups(root_tree)[0].node_tree is wrapper[0]
    assert _nested_groups(wrapper[0])[0].node_tree is leaf[0]
    assert not any(n.bl_idname == 'GeometryNodeMeshCube' for n in root_tree.nodes)


def test_explicit_local_module_import_materializes_canonical_definition(tmp_path):
    from NodeForge import catalog
    from NodeForge.local_index import LocalSourceIndex
    from NodeForge.resolved_environment import ResolvedEnvironment, ResolvedCatalog
    local_file = tmp_path / 'local_geometry.nf'
    local_file.write_text('''def local_mod_leaf(x: Float) -> Float:
    return x + 5

cube(1)
''', encoding='utf-8')
    index = LocalSourceIndex.from_discovery(*catalog.discover_local_sources((tmp_path,)))
    environment = ResolvedEnvironment({
        name: ResolvedCatalog(name, {}, local_index=index if name == 'local' else None)
        for name in ('functions', 'examples', 'local')
    })
    backend = compiler._new_group_backend(environment)
    root = _compose_module('''from local import local_geometry
v = local_geometry.local_mod_leaf(3)
v = v
''', 'NFTest_local_module_imported', environment=environment)
    groups = _module_groups('local_mod_leaf')
    assert len(groups) == 1
    assert _nested_groups(root)[0].node_tree is groups[0]
    assert not any(node.bl_idname == 'GeometryNodeMeshCube' for node in root.nodes)
