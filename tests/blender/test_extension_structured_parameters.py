"""Physical declarative containers, static defaults and durable grouped leaf identity."""
import bpy
import pytest

from NodeForge import compiler
from NodeForge.blender.interface import _get_group_input_declarations
from NodeForge.errors import CompileError
from helpers import compile_group
from test_extension_boundary_matrix import _package

INTERFACE = '''from typing import Annotated
from NodeForge import Float, Int, String, EvaluationMode, DynamicResults
EXTENSION_API=2
EXTENSIONS={'combine': '.backend:combine'}
def combine(items: Annotated[dict[str, Float | Int], EvaluationMode.COMPILE_TIME_OR_RUNTIME],
            *, extra: Annotated[list[Float], EvaluationMode.COMPILE_TIME_OR_RUNTIME],
            labels: Annotated[list[String], EvaluationMode.COMPILE_TIME_ONLY] = ['a,b']) -> DynamicResults: ...
'''
SEMANTIC = '''from NodeForge.nf_types import NFType
def _result_types_combine(args):
    assert type(args['items']) is dict and type(args['extra']) is list
    assert args['labels'][0].value == 'a,b'
    assert [a.typ for a in args['items'].values()] == [NFType.FLOAT]*len(args['items'])
    args['items'].clear()
    args['extra'].clear()
    args['labels'].clear()
    return NFType.FLOAT
'''
BACKEND = '''from NodeForge.nf_types import NFType
from NodeForge.extension_api import ExtensionBackendValue
def combine(context, items, extra, labels):
    assert type(items) is dict and type(extra) is list and labels == ['a,b']
    a = items['runtime']
    assert isinstance(a, ExtensionBackendValue)
    assert type(items['weight']) is float
    node = context.group.nodes.new('ShaderNodeMath')
    node.operation = 'ADD'
    context.group.links.new(a.socket, node.inputs[0])
    node.inputs[1].default_value = items['weight']
    assert len(extra) == 1 and isinstance(extra[0], ExtensionBackendValue)
    items.clear(); extra.clear(); labels.clear()
    return context.value(node.outputs[0], NFType.FLOAT)
'''


@pytest.mark.parametrize('inline',[True,False])
def test_mixed_leaves_static_native_defaults_and_runtime_inputs(tmp_path, inline):
    _package(tmp_path,name='structured',interface=INTERFACE,semantic=SEMANTIC,backend=BACKEND)
    source = f'''from packages import structured

def main(a: Float, b: Float):
    labels = ["a,b"]
    return combine({{"runtime": a, "weight": 0.75}}, extra=[b], labels=labels, __inline__={inline})
'''
    root = compile_group(source,'StructuredMixed')
    child = root if inline else next(n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup')
    math = [n for n in child.nodes if n.bl_idname == 'ShaderNodeMath']
    assert len(math) == 1
    assert math[0].inputs[1].default_value == 0.75
    assert math[0].inputs[0].is_linked
    assert not any(n.bl_idname == 'ShaderNodeValue' for n in child.nodes)
    if not inline:
        node = next(n for n in root.nodes if n.bl_idname == 'GeometryNodeGroup')
        assert [s.name for s in node.inputs] == ['items[runtime]', 'extra[0]']
        assert len(_get_group_input_declarations(child)) == 2
        user_tree = bpy.data.node_groups.new('ExternalStructuredUser','GeometryNodeTree')
        user = user_tree.nodes.new('GeometryNodeGroup'); user.node_tree = child
        user.inputs['items[runtime]'].default_value = 5e-7
        expected = user.inputs['items[runtime]'].default_value
        constant = user_tree.nodes.new('ShaderNodeValue')
        user_tree.links.new(constant.outputs[0], user.inputs['extra[0]'])
        compiler.update_expression_group(root,source.replace('"runtime": a, "weight": 0.75','"weight": 0.5, "runtime": a'))
        assert user.node_tree == child
        assert user.inputs['items[runtime]'].default_value == expected
        assert user.inputs['extra[0]'].links[0].from_node == constant


@pytest.mark.parametrize('inline',[True,False])
def test_runtime_list_display_and_empty_dictionary(tmp_path, inline):
    _package(tmp_path,name='structured_runtime',interface='''from typing import Annotated
from NodeForge import Float, EvaluationMode
EXTENSION_API=2
EXTENSIONS={'first': '.backend:first'}
def first(items: Annotated[list[Float], EvaluationMode.RUNTIME_ONLY],
          *, empty: Annotated[dict[str, Float], EvaluationMode.COMPILE_TIME_OR_RUNTIME] = {}) -> Float: ...
''',backend='''def first(context, items, empty):
    assert type(items) is list and type(empty) is dict and not empty
    return items[0]
''')
    root = compile_group(f'''from packages import structured_runtime

def main(a: Float):
    return first([a], __inline__={inline})
''','StructuredRuntimeList')
    if not inline:
        assert [s.name for n in root.nodes if n.bl_idname == 'GeometryNodeGroup' for s in n.inputs] == ['items[0]']
    with pytest.raises(CompileError,match='list display'):
        compile_group('''from packages import structured_runtime

def main():
    items = [1.0]
    return first(items)
''','BadRuntimeList')


def test_grouped_dictionary_reorder_long_labels_and_exact_values(tmp_path):
    _package(tmp_path,name='structured_keys',interface='''from typing import Annotated
from NodeForge import Float, Color, Vector, String, Rotation, EvaluationMode
EXTENSION_API=2
EXTENSIONS={'select': '.backend:select'}
def select(items: Annotated[dict[str, Float | Color | Vector | String | Rotation], EvaluationMode.RUNTIME_ONLY]) -> Float: ...
''',backend='''def select(context, items):
    return items['x']
''')
    long = '界'*20
    items = f'"x": a, "{long}": b, "s": s, "tint": tint, "rot": rot'
    def source(body):
        return f'''from packages import structured_keys

def main(a: Float, b: Vector, s: String, tint: Color, rot: Rotation):
    return select({{{body}}}, __inline__=False)
'''
    root = compile_group(source(items),'StructuredExternalDefaults')
    child = next(n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup')
    ext = bpy.data.node_groups.new('StructuredExternal','GeometryNodeTree')
    user = ext.nodes.new('GeometryNodeGroup'); user.node_tree = child
    long_label = user.inputs[1].name
    assert len(long_label.encode()) <= 63 and '~' in long_label
    values = {'items[x]':5e-7, long_label:(5e-7,0.,0.), 'items[s]':'123', 'items[tint]':(0.,0.,0.,0.), 'items[rot]':(5e-7,0.,0.)}
    from NodeForge.blender.update import _copy_socket_default_value
    for name, value in values.items():
        user.inputs[name].default_value = value
    before = {name:_copy_socket_default_value(user.inputs[name]) for name in values}
    compiler.update_expression_group(root,source(', '.join(reversed(items.split(', ')))))
    assert user.node_tree == child
    assert {name:_copy_socket_default_value(user.inputs[name]) for name in values} == before
    assert [s.name for s in user.inputs] == list(reversed(list(values)))


@pytest.mark.parametrize('savepoint',[False,True])
def test_structured_external_id_defaults_and_parent_cutover_rollback(tmp_path, savepoint):
    from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
    from helpers import prepared_create_or_update
    _package(tmp_path,name='structured_ids',interface='''from typing import Annotated
from NodeForge import Float, Object, Material, String, EvaluationMode
EXTENSION_API=2
EXTENSIONS={'select': '.backend:select'}
def select(items: Annotated[dict[str, Float | Object | Material | String], EvaluationMode.COMPILE_TIME_OR_RUNTIME]) -> Float: ...
''',backend='''from NodeForge.nf_types import NFType
def select(context, items):
    node = context.group.nodes.new('ShaderNodeMath')
    node.operation = 'ADD'
    context.group.links.new(items['value'].socket, node.inputs[0])
    node.inputs[1].default_value = items['weight']
    return context.value(node.outputs[0], NFType.FLOAT)
''')
    def source(weight, reverse=False):
        items = ['"value": position().x','"object": ob','"material": mat','"string": s',f'"weight": {weight}']
        if reverse:
            items.reverse()
        return '''from packages import structured_ids

def main(ob: Object, mat: Material, s: String):
    return point(vector(select({'''+', '.join(items)+'''}, __inline__=False),0.,0.))
'''
    root = compile_group(source(0.75),'StructuredIDRollback')
    child = next(n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup')
    external = bpy.data.node_groups.new('StructuredIDExternal','GeometryNodeTree')
    user = external.nodes.new('GeometryNodeGroup'); user.node_tree = child
    obj = bpy.data.objects.new('StructuredObj',None)
    material = bpy.data.materials.new('StructuredMat')
    user.inputs['items[object]'].default_value = obj
    user.inputs['items[material]'].default_value = material
    user.inputs['items[string]'].default_value = ''
    user.inputs['items[value]'].default_value = 5e-7
    expected = user.inputs['items[value]'].default_value
    inventory = {g.as_pointer() for g in bpy.data.node_groups}
    tx = BlenderGroupBuildTransaction(); checkpoint = tx.savepoint()
    prepared_create_or_update(source(1.25,True),root.name,existing_group=root,build_options={'local_helper_transaction':tx})
    assert user.inputs['items[object]'].default_value == obj
    assert user.inputs['items[material]'].default_value == material
    assert user.inputs['items[string]'].default_value == ''
    assert user.inputs['items[value]'].default_value == expected
    if savepoint:
        tx.rollback_to_savepoint(checkpoint); tx.commit()
    else:
        tx.rollback()
    assert [s.name for s in user.inputs] == ['items[value]','items[object]','items[material]','items[string]']
    assert user.inputs['items[object]'].default_value == obj and user.inputs['items[material]'].default_value == material
    assert user.inputs['items[string]'].default_value == '' and user.inputs['items[value]'].default_value == expected
    assert user.node_tree == child and {g.as_pointer() for g in bpy.data.node_groups} == inventory


def test_selected_declaration_rejects_forged_structured_transport():
    """The physical boundary must not guess a static type or accept wrong runtime ownership."""
    from dataclasses import replace
    import inspect
    from types import SimpleNamespace
    from NodeForge.blender.extension_backend import _bound_arguments
    from NodeForge.blender.values import make_value
    from NodeForge.evaluation_modes import EvaluationMode
    from NodeForge.extensions.contracts import ExtensionCallableId, ExtensionCallableSpec, ExtensionParameterSpec, TypeSpec
    from NodeForge.nf_types import NFType
    from NodeForge.semantic.ir import IRCallArgument, IRStaticCallArgument, IRValue
    scalar = TypeSpec('NF_SET',frozenset({NFType.FLOAT}))
    parameter = ExtensionParameterSpec('items',inspect.Parameter.POSITIONAL_OR_KEYWORD,TypeSpec('DICT_STR',item=scalar),EvaluationMode.COMPILE_TIME_OR_RUNTIME)
    spec = ExtensionCallableSpec(ExtensionCallableId(('system','test'),'foo'),(parameter,),scalar)
    group = bpy.data.node_groups.new('ForgedTransport','GeometryNodeTree')
    other = bpy.data.node_groups.new('ForeignTransport','GeometryNodeTree')
    value = make_value(group.nodes.new('ShaderNodeValue').outputs[0],NFType.FLOAT)
    foreign = make_value(other.nodes.new('ShaderNodeValue').outputs[0],NFType.FLOAT)
    argument = IRCallArgument('items',IRValue(0,NFType.FLOAT),0,0)
    static = IRStaticCallArgument(0,0.75,1,NFType.FLOAT)
    operation = SimpleNamespace(structured_arguments=((0,('runtime','weight')),),arguments=(argument,),static_arguments=(static,))
    bound = _bound_arguments(spec,operation,[value],group)
    assert bound.arguments['items']['weight'] == 0.75
    with pytest.raises(CompileError):
        _bound_arguments(spec,operation,[foreign],group)
    with pytest.raises(CompileError):
        _bound_arguments(spec,operation,[],group)
    operation.static_arguments = (replace(static,value=1),)
    with pytest.raises(CompileError,match='canonical'):
        _bound_arguments(spec,operation,[value],group)
    operation.static_arguments = (static,)
    operation.structured_arguments = ((0,(0,1)),)
    with pytest.raises(CompileError,match='key kind'):
        _bound_arguments(spec,operation,[value],group)
