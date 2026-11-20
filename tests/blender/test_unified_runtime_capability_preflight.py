"""ROOT/NESTED semantic roles and physical Extension API v2 base-tree isolation."""

from helpers import *
import builtins
import json
from NodeForge import packages


def _install_capability_fixture(tmp_path):
    """Install a physical v2 owner with dangerous and valid implementations."""
    root = tmp_path / 'danger_extension'
    owner = root / 'systems' / 'danger'
    owner.mkdir(parents=True)
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.runtime.danger', 'name': 'Runtime boundary',
        'version': '1.0.0', 'author': 'Tests',
        'description': 'Root capability negative / nested positive',
        'nodeforge_min_version': '0.65.0', 'nodeforge_max_version': None,
        'contents': {'systems': 'systems'}, 'permissions': {'python': True},
    }), encoding='utf-8')
    (owner / 'interface.py').write_text('''from typing import Annotated
from NodeForge import Float, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"unsafe_wipe": ".operations:unsafe_wipe", "safe_add": ".operations:safe_add"}
def unsafe_wipe(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def safe_add(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
''', encoding='utf-8')
    (owner / 'operations.py').write_text('''import builtins
from NodeForge.extension_api import NFType

def unsafe_wipe(context, value):
    builtins._nodeforge_direct_danger_hits = getattr(builtins, "_nodeforge_direct_danger_hits", 0) + 1
    context.group.nodes.clear()
    node = context.group.nodes.new("ShaderNodeValue")
    return context.value(node.outputs[0], NFType.FLOAT)

def safe_add(context, value):
    builtins._nodeforge_nested_safe_hits = getattr(builtins, "_nodeforge_nested_safe_hits", 0) + 1
    node = context.group.nodes.new("ShaderNodeMath")
    node.operation = "ADD"
    context.group.links.new(value.socket, node.inputs[0])
    node.inputs[1].default_value = 2.0
    return context.value(node.outputs[0], NFType.FLOAT)
''', encoding='utf-8')
    packages.install_package_directory(root)


def test_direct_physical_extension_never_invokes_destructive_backend(tmp_path):
    """Preflight executes before any level-zero node or extension side effect."""
    _install_capability_fixture(tmp_path)
    if hasattr(builtins, '_nodeforge_direct_danger_hits'):
        delattr(builtins, '_nodeforge_direct_danger_hits')
    group = bpy.data.node_groups.new('NFTest_extension_direct_forbidden', 'GeometryNodeTree')
    a = group.nodes.new('GeometryNodeMeshCube')
    b = group.nodes.new('GeometryNodeTransform')
    group.links.new(a.outputs['Mesh'], b.inputs['Geometry'])
    before = {node.as_pointer() for node in group.nodes}
    with pytest.raises(CompileError, match='unsupported directly'):
        compile_root('''from packages import danger
first = cube(1)
result = unsafe_wipe(1.0)
''', group)
    assert not hasattr(builtins, '_nodeforge_direct_danger_hits')
    assert {node.as_pointer() for node in group.nodes} == before
    assert len(group.links) == 1
    assert group.links[0].from_node.as_pointer() == a.as_pointer()
    assert group.links[0].to_node.as_pointer() == b.as_pointer()


def test_physical_extension_inside_nested_function_uses_own_group_transaction(tmp_path):
    """A nested v2 extension remains valid when no user-owned base tree is exposed."""
    _install_capability_fixture(tmp_path)
    builtins._nodeforge_nested_safe_hits = 0
    group = bpy.data.node_groups.new('NFTest_nested_extension_supported', 'GeometryNodeTree')
    previous = group.nodes.new('GeometryNodeMeshCube')
    compile_root('''from packages import danger
def nested_safe(x: Float) -> Float:
    return safe_add(x)
value = nested_safe(1.0)
''', group)
    assert builtins._nodeforge_nested_safe_hits == 1
    assert all(node.as_pointer() != previous.as_pointer() for node in group.nodes)
    nested = [node.node_tree for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(nested) == 1
    assert any(node.bl_idname == 'ShaderNodeMath' for node in nested[0].nodes)


def test_physical_extension_in_transitively_inline_root_is_rejected_before_backend(tmp_path):
    """PR1 must not pass the authoritative root into nested physical Python backends."""
    _install_capability_fixture(tmp_path)
    if hasattr(builtins, '_nodeforge_direct_danger_hits'):
        delattr(builtins, '_nodeforge_direct_danger_hits')
    root = bpy.data.node_groups.new('NFTest_inline_extension_root_forbidden', 'GeometryNodeTree')
    manual = root.nodes.new('GeometryNodeMeshCube')
    before = {node.as_pointer() for node in root.nodes}
    with pytest.raises(CompileError):
        compile_root('''from packages import danger

def inline_unsafe(value: Float, __inline__=True) -> Float:
    return unsafe_wipe(value)

result = inline_unsafe(1.0)
''', root)
    assert not hasattr(builtins, '_nodeforge_direct_danger_hits')
    assert {node.as_pointer() for node in root.nodes} == before
    assert manual.as_pointer() in before
