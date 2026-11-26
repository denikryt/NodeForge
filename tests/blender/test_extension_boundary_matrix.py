"""Cross-boundary dynamic signatures and Capture/Rotation publication contracts."""

from pathlib import Path
import json

import bpy
import pytest

from NodeForge import compiler, packages
from NodeForge.blender.extension_group_materializer import EXTENSION_AUTHORITY_PROP
from NodeForge.blender.root_update import RootUpdateError
from NodeForge.errors import CompileError


def _package(tmp_path, *, name, interface, backend, semantic=None):
    """Install a synthetic v2 owner using the same registry as public Compile."""
    root = tmp_path / name
    owner = root / 'systems' / name
    owner.mkdir(parents=True)
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.extension.' + name,
        'name': name, 'version': '1.0.0', 'author': 'Tests',
        'description': 'Extension boundary matrix', 'nodeforge_min_version': '0.96.0',
        'nodeforge_max_version': None, 'contents': {'systems': 'systems'},
        'permissions': {'python': True},
    }), encoding='utf-8')
    (owner / 'interface.py').write_text(interface, encoding='utf-8')
    (owner / 'backend.py').write_text(backend, encoding='utf-8')
    if semantic is not None:
        (owner / 'semantic.py').write_text(semantic, encoding='utf-8')
    packages.install_package_directory(root)
    return root


_DYNAMIC_INTERFACE = '''from typing import Annotated
from NodeForge import Float, String, Vector, DynamicResults, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {'choose': '.backend:choose'}
def choose(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
           form: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT') -> DynamicResults: ...
'''
_DYNAMIC_SEMANTIC = '''from NodeForge.nf_types import NFType
def _result_types_choose(args):
    return {'value': NFType.VECTOR if args['form'].value == 'VECTOR' else NFType.FLOAT}
'''
_DYNAMIC_BACKEND = '''from NodeForge.nf_types import NFType

def choose(context, value, form='FLOAT'):
    assert context.result_types == ((NFType.VECTOR,) if form == 'VECTOR' else (NFType.FLOAT,))
    if form == 'VECTOR':
        node = context.group.nodes.new('ShaderNodeCombineXYZ')
        context.group.links.new(value.socket, node.inputs['X'])
        return {'value': context.value(node.outputs[0], NFType.VECTOR)}
    return {'value': value}
'''


def test_dynamic_results_overloads_remain_distinct_named_results_across_root_grouped_updates(tmp_path):
    _package(tmp_path, name='extension_dynamic_types', interface=_DYNAMIC_INTERFACE,
             semantic=_DYNAMIC_SEMANTIC, backend=_DYNAMIC_BACKEND)
    root = bpy.data.node_groups.new('NFExt_dynamic_matrix_root', 'GeometryNodeTree')
    source = '''from packages import extension_dynamic_types
a = choose(1.0, form='FLOAT', __inline__=False).value
b = choose(2.0, form='VECTOR', __inline__=False).value
'''
    compiler.compile_source_in_tree(source, root)
    groups = [node for node in root.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(groups) == 2
    groups_by_type = {n.outputs[0].bl_idname: n.node_tree for n in groups}
    assert set(groups_by_type) == {'NodeSocketFloat', 'NodeSocketVector'}
    assert all([s.name for s in n.outputs] == ['value'] for n in groups)
    authority = {g[EXTENSION_AUTHORITY_PROP]: g.as_pointer() for g in groups_by_type.values()}
    assert len(authority) == 2
    float_group = groups_by_type['NodeSocketFloat']
    manual_tree = bpy.data.node_groups.new('NFExt_dynamic_manual_ref', 'GeometryNodeTree')
    manual_node = manual_tree.nodes.new('GeometryNodeGroup')
    manual_node.node_tree = float_group
    src2 = source.replace("choose(1.0, form='FLOAT', __inline__=False)",
                          "choose(1.0, form='VECTOR', __inline__=False)")
    compiler.compile_source_in_tree(src2, root)
    updated = [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
    assert len(updated) == 2
    assert {g[EXTENSION_AUTHORITY_PROP]: g.as_pointer() for g in updated} == authority
    assert manual_node.node_tree.as_pointer() == float_group.as_pointer()
    assert manual_node.outputs[0].bl_idname == 'NodeSocketVector'
    assert all(n.outputs[0].bl_idname == 'NodeSocketVector' for n in root.nodes
               if n.bl_idname == 'GeometryNodeGroup')


_CAPTURE_INTERFACE = '''from typing import Annotated
from NodeForge import EvaluationMode, Geometry
EXTENSION_API = 2
EXTENSIONS = {'capture': '.backend:capture'}
def capture(geometry: Annotated[Geometry, EvaluationMode.RUNTIME_ONLY]) -> Geometry: ...
'''
_CAPTURE_BACKEND = '''from NodeForge.nf_types import NFType

def capture(context, geometry):
    group = context.group
    cap = group.nodes.new('GeometryNodeCaptureAttribute')
    cap.capture_items.clear()
    for typ, name in (
        ('VECTOR', 'Pos'), ('INT', 'Index'), ('FLOAT', 'Alpha'),
        ('FLOAT', 'Beta'), ('BOOLEAN', 'Selected'), ('RGBA', 'Tint'),
        ('ROTATION', 'Spin'),
    ):
        cap.capture_items.new(typ, name)
    cap.domain = 'POINT'
    cap.inputs['Beta'].default_value = 0.75
    group.links.new(geometry.socket, cap.inputs['Geometry'])
    pos = group.nodes.new('GeometryNodeInputPosition')
    idx = group.nodes.new('GeometryNodeInputIndex')
    group.links.new(pos.outputs['Position'], cap.inputs['Pos'])
    group.links.new(idx.outputs['Index'], cap.inputs['Index'])
    move = group.nodes.new('GeometryNodeTransform')
    group.links.new(cap.outputs['Geometry'], move.inputs['Geometry'])
    convert = group.nodes.new('FunctionNodeRotationToEuler')
    group.links.new(cap.outputs['Spin'], convert.inputs['Rotation'])
    return context.value(move.outputs['Geometry'], NFType.GEOMETRY)
'''


def _capture_assertions(group):
    cap, = [n for n in group.nodes if n.bl_idname == 'GeometryNodeCaptureAttribute']
    assert [(item.name,item.data_type) for item in cap.capture_items] == [
        ('Pos', 'FLOAT_VECTOR'), ('Index', 'INT'), ('Alpha', 'FLOAT'),
        ('Beta', 'FLOAT'), ('Selected', 'BOOLEAN'), ('Tint', 'FLOAT_COLOR'),
        ('Spin', 'QUATERNION'),
    ]
    assert cap.inputs['Spin'].bl_idname == cap.outputs['Spin'].bl_idname == 'NodeSocketRotation'
    assert cap.inputs['Tint'].bl_idname == cap.outputs['Tint'].bl_idname == 'NodeSocketColor'
    assert cap.inputs['Beta'].default_value == pytest.approx(0.75)
    assert len([l for l in group.links if l.to_node == cap]) == 3
    assert any(l.from_node == cap and l.from_socket.name == 'Spin' for l in group.links)
    assert all(l.is_valid for l in group.links)


def test_capture_rotation_clone_and_update_through_root_and_group_boundaries(tmp_path):
    _package(tmp_path, name='extension_capture_mix', interface=_CAPTURE_INTERFACE,
             backend=_CAPTURE_BACKEND)
    root = bpy.data.node_groups.new('NFExt_capture_matrix_root', 'GeometryNodeTree')
    inline = 'from packages import extension_capture_mix\ngeo = cube(1)\nout = capture(geo)\n'
    compiler.compile_source_in_tree(inline, root)
    _capture_assertions(root)
    assert len([n for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']) == 0
    grouped = 'from packages import extension_capture_mix\ngeo = cube(1)\nout = capture(geo,__inline__=False)\n'
    compiler.compile_source_in_tree(grouped, root)
    sub, = [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
    _capture_assertions(sub)
    pointer = sub.as_pointer()
    compiler.compile_source_in_tree(grouped, root)
    again, = [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
    assert again.as_pointer() == pointer
    _capture_assertions(again)


def test_capture_root_copy_error_preserves_previous_graph_and_resources(tmp_path, monkeypatch):
    from NodeForge.blender import group_backend
    _package(tmp_path, name='extension_capture_reject', interface=_CAPTURE_INTERFACE,
             backend=_CAPTURE_BACKEND)
    root = bpy.data.node_groups.new('NFExt_capture_matrix_rollback', 'GeometryNodeTree')
    old = root.nodes.new('GeometryNodeMeshCube')
    before = old.as_pointer()

    def reject_capture(*args):
        raise CompileError('Extension Capture item reconstruction blocked')

    monkeypatch.setattr(group_backend, '_copy_standalone_collection', reject_capture)
    source = 'from packages import extension_capture_reject\ngeo=cube(1)\nout=capture(geo)\n'
    with pytest.raises(RootUpdateError, match='Extension Capture item reconstruction blocked') as info:
        compiler.compile_source_in_tree(source, root)
    assert not info.value.may_have_mutated
    assert len(root.nodes) == 1 and root.nodes[0].as_pointer() == before


def test_grouped_extension_identity_survives_actual_blend_save_reopen(tmp_path):
    """A reopened root keeps physical UUID and updates the same child datablock."""
    import subprocess
    import sys
    from textwrap import dedent

    package_root = _package(tmp_path, name='extension_reopen', interface=_DYNAMIC_INTERFACE,
                            semantic=_DYNAMIC_SEMANTIC, backend=_DYNAMIC_BACKEND)
    project = Path(__file__).resolve().parents[2]
    blend_file = tmp_path / 'root_reopen.blend'
    script = tmp_path / 'reopen_probe.py'
    script.write_text(dedent(f'''
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(project.parent)!r})
        import bpy
        from NodeForge import compiler, packages
        from NodeForge.blender.extension_group_materializer import EXTENSION_AUTHORITY_PROP
        from NodeForge.function_instances import COMPOSITION_ROOT_ID_PROP
        # The package may already be registered by the parent Blender process.
        try:
            packages.install_package_directory(Path({str(package_root)!r}))
        except packages.PackageError as exc:
            if 'already installed' not in str(exc):
                raise
        src = "from packages import extension_reopen\\na = choose(1.0, __inline__=False).value\\n"
        root = bpy.data.node_groups.new('NFExt_reopen_grouped_root', 'GeometryNodeTree')
        root.use_fake_user = True
        compiler.compile_source_in_tree(src, root, module_context='text:extension-reopen')
        original = root[COMPOSITION_ROOT_ID_PROP]
        groups = [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
        assert len(groups) == 1 and groups[0][EXTENSION_AUTHORITY_PROP]
        assert bpy.ops.wm.save_as_mainfile(filepath={str(blend_file)!r}) == {{'FINISHED'}}
        assert bpy.ops.wm.open_mainfile(filepath={str(blend_file)!r}) == {{'FINISHED'}}
        root = bpy.data.node_groups['NFExt_reopen_grouped_root']
        assert root[COMPOSITION_ROOT_ID_PROP] == original
        groups = [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
        assert len(groups) == 1
        prior = groups[0].as_pointer()
        authority = groups[0][EXTENSION_AUTHORITY_PROP]
        root.name = 'NFExt_reopen_grouped_root_renamed'
        compiler.compile_source_in_tree(src, root, module_context='text:extension-reopen')
        groups = [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
        assert len(groups) == 1 and groups[0].as_pointer() == prior
        assert groups[0][EXTENSION_AUTHORITY_PROP] == authority
        assert root[COMPOSITION_ROOT_ID_PROP] == original
        assert sum(g.get(EXTENSION_AUTHORITY_PROP) == authority for g in bpy.data.node_groups) == 1
        print('NFExt_SAVE_REOPEN_PASS')
    '''), encoding='utf-8')
    finished = subprocess.run([bpy.app.binary_path, '-b', '-noaudio', '--python', str(script)],
                              capture_output=True, text=True, timeout=60)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert 'NFExt_SAVE_REOPEN_PASS' in finished.stdout


def test_variadic_dynamic_results_keep_heterogeneous_type_order_and_dependency_pins(tmp_path):
    interface = '''from typing import Annotated
from NodeForge import Float, Vector, Int, DynamicResults, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {'many': '.backend:many'}
def many(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
         *values: Annotated[Float | Vector | Int, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
'''
    semantic = '''def _result_types_many(args):
    return {'value': args['value'].typ, **{f'part_{i}': part.typ for i, part in enumerate(args['values'])}}
'''
    backend = '''def many(context, value, *values):
    assert context.result_types == (value.typ, *(part.typ for part in values))
    return {'value': value, **{f'part_{i}': part for i, part in enumerate(values)}}
'''
    _package(tmp_path, name='extension_variadic_results', interface=interface,
             semantic=semantic, backend=backend)
    root = bpy.data.node_groups.new('NFExt_variadic_dynamic_root', 'GeometryNodeTree')
    compiler.compile_source_in_tree('''from packages import extension_variadic_results
first = many(1.0, vector(1, 2, 3), __inline__=False)
a = first.value
b = first.part_0
second = many(2.0, 3, 4, __inline__=False)
c = second.value
d = second.part_0
e = second.part_1
''', root)
    nodes = [n for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
    assert len(nodes) == 2
    by_arity = {len(n.outputs): n for n in nodes}
    assert set(by_arity) == {2, 3}
    assert [(s.name, s.bl_idname) for s in by_arity[2].outputs] == [
        ('value', 'NodeSocketFloat'), ('part_0', 'NodeSocketVector')]
    assert [(s.name, s.bl_idname) for s in by_arity[3].outputs] == [
        ('value', 'NodeSocketFloat'), ('part_0', 'NodeSocketInt'),
        ('part_1', 'NodeSocketInt')]
    assert [s.name for s in by_arity[2].inputs] == ['value', 'values_0']
    assert [s.name for s in by_arity[3].inputs] == ['value', 'values_0', 'values_1']
