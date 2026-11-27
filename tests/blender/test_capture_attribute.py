from helpers import *


def _capture_nodes(group):
    return [node for node in group.nodes if getattr(node, "bl_idname", None) == "GeometryNodeCaptureAttribute"]


def test_capture_attribute_supports_existing_nodeforge_field_types():
    group = compile_group('''
def nf_fixture():
    geo = grid(2, 2)
    geo, captured_float = capture_attribute(geo, position().x)
    geo, captured_int = capture_attribute(geo, index())
    geo, captured_bool = capture_attribute(geo, position().x > 0.0)
    geo, captured_vector = capture_attribute(geo, position())
    geometry = geo
    float = captured_float
    int = captured_int
    bool = captured_bool
    captured_vector_value = captured_vector
    return (geometry, float, int, bool, captured_vector_value,)
''', "NFTest_capture_attribute_supported_types")

    nodes = _capture_nodes(group)
    check(len(nodes) == 4, f"expected 4 Capture Attribute nodes, got {len(nodes)}")
    data_types = [node.capture_items[0].data_type for node in nodes]
    check(data_types == ["FLOAT", "INT", "BOOLEAN", "FLOAT_VECTOR"], f"unexpected capture data types: {data_types}")
    for node in nodes:
        selection = node.inputs.get("Selection")
        check(selection is not None, "Capture Attribute Selection input missing")
        check(node.domain == "POINT", "omitted capture domain did not remain POINT")
        check(not selection.is_linked, "omitted Capture Attribute selection unexpectedly linked")
        check(bool(selection.default_value) is True, "omitted Capture Attribute selection is not explicitly True")


def test_capture_attribute_color_constructor_and_override():
    """Color values use RGBA constructors and FLOAT_COLOR persisted item tokens."""
    group = compile_group('''
def nf_fixture(color: Color):
    geo = grid(2, 2)
    geo, captured = capture_attribute(geo, color, type="RGBA")
    return (geo, captured)
''' , "NFTest_capture_attribute_color")
    nodes = _capture_nodes(group)
    check(len(nodes)==1, "Color Capture node missing")
    check(nodes[0].capture_items[0].data_type=='FLOAT_COLOR', "Color persisted type mismatch")
    check(nodes[0].outputs['Value'].bl_idname=='NodeSocketColor', "Color output socket mismatch")
    check(all(link.is_valid for link in group.links), "Color Capture has invalid Blender links")
    expect_compile_error('''
def nf_fixture(color: Color):
    geo = grid(2,2)
    geo, captured = capture_attribute(geo, color, type="FLOAT_COLOR")
    return (geo,captured)
''' , "NFTest_capture_invalid_constructor_token")


def test_capture_attribute_rejects_blender_domains_outside_nodeforge_contract():
    expect_compile_error('''
def nf_fixture():
    geo = grid(2, 2)
    geo, captured = capture_attribute(geo, position().x, domain="LAYER")
    geometry = geo
    return (geometry,)
''', "NFTest_capture_attribute_layer_domain_unsupported")


def test_local_geometry_helper_inside_repeat_uses_declared_output_socket():
    group = compile_entry_group('''
def add_empty(geometry: Geometry, selection: Bool):
    selected = node(
        "GeometryNodeSeparateGeometry",
        props={"domain": "POINT"},
        inputs={"Geometry": geometry, "Selection": selection},
        output="Selection",
        typ=Geometry,
    )
    return join(selected, empty_geometry())

def nf_fixture(iterations: Int["Iterations"] = 2):
    result = points(1)
    for i in repeat_range(iterations):
        result = add_empty(result, i >= 0)
    return result
''', "NFTest_local_geometry_helper_repeat_output_direction", "nf_fixture")
    check(any(node.bl_idname == "GeometryNodeRepeatOutput" for node in group.nodes), "Repeat Zone output missing")


def test_store_named_attribute_omitted_selection_is_explicit_select_all():
    group = compile_group('''
def nf_fixture():
    geo = grid(2, 2)
    geo = store_named_attribute(geo, "weight", position().x)
    geometry = geo
    return (geometry,)
''', "NFTest_store_selection_default")
    nodes = [node for node in group.nodes if node.bl_idname == "GeometryNodeStoreNamedAttribute"]
    check(len(nodes) == 1, f"expected one Store Named Attribute node, got {len(nodes)}")
    selection = nodes[0].inputs.get("Selection")
    check(selection is not None, "Store Named Attribute Selection input missing")
    check(not selection.is_linked, "omitted Store selection unexpectedly linked")
    check(bool(selection.default_value) is True, "omitted Store selection is not explicitly True")


@pytest.mark.parametrize('constructor,persisted,physical', [
    ('FLOAT', 'FLOAT', 'NodeSocketFloat'),
    ('VECTOR', 'FLOAT_VECTOR', 'NodeSocketVector'),
    ('RGBA', 'FLOAT_COLOR', 'NodeSocketColor'),
    ('ROTATION', 'QUATERNION', 'NodeSocketRotation'),
])
@pytest.mark.parametrize('role', ['backup', 'cutover'])
@pytest.mark.parametrize('linked', [True, False])
def test_capture_copy_preserves_linked_and_unlinked_item_contract(
    constructor, persisted, physical, role, linked
):
    """Transactional copies must preserve every dynamic Capture item and its links."""
    src = bpy.data.node_groups.new('NFTest_capture_copy_source', 'GeometryNodeTree')
    capture = src.nodes.new('GeometryNodeCaptureAttribute')
    capture.capture_items.new(constructor, 'Stored')
    assert capture.capture_items[0].data_type == persisted
    if linked:
        src.interface.new_socket(name='Stored',in_out='OUTPUT',socket_type=physical)
        src_output = src.nodes.new('NodeGroupOutput')
        src.links.new(capture.outputs['Stored'], src_output.inputs['Stored'])
        assert all(link.is_valid for link in src.links)
    dst = bpy.data.node_groups.new('NFTest_capture_copy_target', 'GeometryNodeTree')
    blender_group_backend._copy_group_contents(src, dst, copy_role=role)
    restored = next(n for n in dst.nodes if n.bl_idname=='GeometryNodeCaptureAttribute')
    assert [(item.name,item.data_type) for item in restored.capture_items] == [('Stored',persisted)]
    assert restored.outputs['Stored'].bl_idname == physical
    assert len(dst.links) == (1 if linked else 0)
    assert all(link.is_valid for link in dst.links)


def test_capture_copy_restores_persisted_quaternion_without_modifying_source():
    """A persisted QUATERNION item recreates via ROTATION, even without links."""
    src = bpy.data.node_groups.new('NFTest_capture_rotation_source', 'GeometryNodeTree')
    cap = src.nodes.new('GeometryNodeCaptureAttribute')
    cap.capture_items.new('ROTATION', 'Spin')
    assert cap.capture_items[0].data_type == 'QUATERNION'
    dst = bpy.data.node_groups.new('NFTest_capture_rotation_destination', 'GeometryNodeTree')
    blender_group_backend._copy_group_contents(src, dst, copy_role='backup')
    copied, = [node for node in dst.nodes if node.bl_idname == 'GeometryNodeCaptureAttribute']
    assert [(item.name, item.data_type) for item in copied.capture_items] == [('Spin', 'QUATERNION')]
    assert copied.inputs['Spin'].bl_idname == 'NodeSocketRotation'
    assert copied.outputs['Spin'].bl_idname == 'NodeSocketRotation'
    assert [(item.name, item.data_type) for item in cap.capture_items] == [('Spin', 'QUATERNION')]


def test_capture_root_update_reconstruction_uses_same_checked_helper():
    """The shared root-update restoration path preserves unlinked Color items."""
    from NodeForge.blender import group_backend
    src = bpy.data.node_groups.new('NFTest_capture_root_reconstruct','GeometryNodeTree')
    old=src.nodes.new('GeometryNodeCaptureAttribute')
    old.capture_items.new('RGBA','Tint')
    old.capture_items.new('ROTATION','Spin')
    backup = bpy.data.node_groups.new('NFTest_capture_backup','GeometryNodeTree')
    cloned=backup.nodes.new('GeometryNodeCaptureAttribute')
    cloned.name=old.name
    group_backend._copy_node_properties(old, cloned)
    assert [(item.name,item.data_type) for item in cloned.capture_items] == [('Tint','FLOAT_COLOR'), ('Spin','QUATERNION')]
    assert cloned.outputs['Tint'].bl_idname == 'NodeSocketColor'
    assert cloned.inputs['Spin'].bl_idname == 'NodeSocketRotation'
    assert cloned.outputs['Spin'].bl_idname == 'NodeSocketRotation'


def test_rotation_capture_builtin_infers_and_accepts_explicit_type():
    """The existing Capture builtin returns a Rotation field with native sockets."""
    for token in ('', ', type="ROTATION"'):
        group = compile_group(f'''
def nf_fixture(value: Rotation):
    geo = grid(2, 2)
    geo, rotation = capture_attribute(geo, value{token})
    return geo, rotation
''', 'NFTest_capture_native_rotation')
        capture, = _capture_nodes(group)
        assert capture.capture_items[0].data_type == 'QUATERNION'
        assert capture.inputs['Value'].bl_idname == 'NodeSocketRotation'
        assert capture.outputs['Value'].bl_idname == 'NodeSocketRotation'
        assert all(link.is_valid for link in group.links)
    expect_compile_error('''
def nf_fixture(value: Rotation):
    geo = grid(2, 2)
    geo, rotation = capture_attribute(geo, value, type="QUATERNION")
    return geo, rotation
''', 'NFTest_capture_wrong_rotation_constructor')


def test_rotation_capture_group_copy_preserves_mixed_items_links_and_defaults():
    """Transaction copy recreates Rotation and neighbors with per-item defaults."""
    from NodeForge.blender.group_backend import _copy_group_contents
    src = bpy.data.node_groups.new('NFTest_capture_rotation_mixed', 'GeometryNodeTree')
    cap = src.nodes.new('GeometryNodeCaptureAttribute')
    for token, name in (('FLOAT', 'Weight'), ('ROTATION', 'Spin'), ('VECTOR', 'Offset')):
        cap.capture_items.new(token, name)
    cap.inputs['Weight'].default_value = 0.25
    cap.inputs['Spin'].default_value = (0.1, 0.2, 0.3)
    cap.inputs['Offset'].default_value = (1.0, 2.0, 3.0)
    src.interface.new_socket(name='Spin',in_out='OUTPUT',socket_type='NodeSocketRotation')
    group_output = src.nodes.new('NodeGroupOutput')
    src.links.new(cap.outputs['Spin'],group_output.inputs['Spin'])
    assert all(link.is_valid for link in src.links)
    for role in ('backup', 'cutover'):
        dst=bpy.data.node_groups.new('NFTest_capture_rotation_copy_'+role, 'GeometryNodeTree')
        _copy_group_contents(src, dst, copy_role=role)
        copied, = _capture_nodes(dst)
        assert [(i.name,i.data_type) for i in copied.capture_items] == [
            ('Weight','FLOAT'),('Spin','QUATERNION'),('Offset','FLOAT_VECTOR')]
        for name, physical in (('Weight','NodeSocketFloat'),('Spin','NodeSocketRotation'),
                               ('Offset','NodeSocketVector')):
            assert copied.inputs[name].bl_idname == physical
            assert copied.outputs[name].bl_idname == physical
        for name, expected in (('Weight',(0.25,)),('Spin',(0.1,0.2,0.3)),
                               ('Offset',(1.0,2.0,3.0))):
            actual=copied.inputs[name].default_value
            if name=='Weight': actual=(actual,)
            assert all(abs(float(a)-b)<1e-4 for a,b in zip(actual,expected))
        assert len(dst.links)==1 and all(link.is_valid for link in dst.links)
        assert dst.links[0].from_socket.bl_idname=='NodeSocketRotation'


def test_capture_rotation_group_update_and_failed_update_keep_contract():
    """Selected group update copies Rotation-backed old state and commits replacement."""
    def source(typ, token=''):
        return f'''
def nf_fixture(value: {typ}):
    geo = grid(2,2)
    geo, captured = capture_attribute(geo, value{token})
    return geo, captured
'''
    group=compile_group(source('Rotation'), 'NFTest_capture_update_rotation')
    pointer=group.as_pointer()
    assert _capture_nodes(group)[0].capture_items[0].data_type == 'QUATERNION'
    with pytest.raises(CompileError):
        compiler.update_expression_group(group, source('Rotation', ', type="QUATERNION"'))
    assert _capture_nodes(group)[0].capture_items[0].data_type == 'QUATERNION'
    compiler.update_expression_group(group, source('Rotation', ', type="ROTATION"'))
    assert group.as_pointer() == pointer
    assert _capture_nodes(group)[0].capture_items[0].data_type == 'QUATERNION'
    compiler.update_expression_group(group, source('Rotation'))
    assert _capture_nodes(group)[0].capture_items[0].data_type == 'QUATERNION'
    assert all(link.is_valid for link in group.links)


def test_rotation_capture_root_update_rollback_restores_links(monkeypatch):
    """Root rollback reconstructs a previous linked QUATERNION item."""
    from NodeForge.blender import root_update
    root = bpy.data.node_groups.new('NFTest_capture_root_rotation', 'GeometryNodeTree')
    root.interface.new_socket(name='Geometry', in_out='INPUT',socket_type='NodeSocketGeometry')
    root.interface.new_socket(name='Geometry', in_out='OUTPUT',socket_type='NodeSocketGeometry')
    root.interface.new_socket(name='Spin', in_out='OUTPUT',socket_type='NodeSocketRotation')
    inlet=root.nodes.new('NodeGroupInput')
    outlet=root.nodes.new('NodeGroupOutput')
    cap=root.nodes.new('GeometryNodeCaptureAttribute')
    cap.capture_items.new('FLOAT', 'Weight')
    cap.capture_items.new('ROTATION', 'Spin')
    cap.capture_items.new('VECTOR', 'Offset')
    root.links.new(inlet.outputs['Geometry'],cap.inputs['Geometry'])
    root.links.new(cap.outputs['Geometry'],outlet.inputs['Geometry'])
    root.links.new(cap.outputs['Spin'],outlet.inputs['Spin'])
    original=[(i.name,i.data_type) for i in cap.capture_items]
    def fail(*args, **kwargs):
        raise RuntimeError('Capture root rollback injection')
    monkeypatch.setattr(root_update,'_reconnect_boundary',fail)
    with pytest.raises(root_update.RootUpdateError,match='Capture root rollback injection'):
        compiler.compile_source_in_tree('a = cube(2)\n', root)
    restored, = _capture_nodes(root)
    assert [(i.name,i.data_type) for i in restored.capture_items]==original
    assert restored.outputs['Spin'].bl_idname=='NodeSocketRotation'
    assert len(root.links)==3 and all(link.is_valid for link in root.links)


@pytest.mark.parametrize('kind,count', [('Capture', 0), ('Capture', 6), ('Menu', 1), ('Menu', 32), ('Index', 0), ('Index', 1), ('Index', 32)])
def test_local_collection_copy_limits_and_exact_defaults(kind, count):
    """All standalone layouts are copied before any values are applied, including empty layouts."""
    ids = {'Capture':'GeometryNodeCaptureAttribute','Menu':'GeometryNodeMenuSwitch','Index':'GeometryNodeIndexSwitch'}
    src = bpy.data.node_groups.new('NF_collection_limits_source', 'GeometryNodeTree')
    dst = bpy.data.node_groups.new('NF_collection_limits_dest', 'GeometryNodeTree')
    node = src.nodes.new(ids[kind])
    if kind == 'Capture':
        for typ, label in [('FLOAT','Geometry'),('BOOLEAN','Selection'),('INT','Index'),('VECTOR','Vector'),('RGBA','Tint'),('ROTATION','Spin')][:count]:
            node.capture_items.new(typ, label)
        values = (.75, True, 7, (.1,.2,.3), (.25,.5,.75,0.), (.1,.2,.3))[:count]
        offset = 2
    elif kind == 'Menu':
        node.data_type = 'FLOAT'
        node.enum_items.clear()
        for i in range(count):
            node.enum_items.new(f'Item {i}').description = f'Description {i}'
        node.inputs[0].default_value = f'Item {count-1}'
        values, offset = tuple(i+.25 for i in range(count)), 1
    else:
        node.index_switch_items.clear()
        for i in range(count):
            node.index_switch_items.new()
        node.inputs[0].default_value = max(0,count-1)
        values, offset = tuple(i+.25 for i in range(count)), 1
    for index,value in enumerate(values, offset):
        node.inputs[index].default_value = value
    def defaults(n):
        return tuple(tuple(v) if hasattr(v,'__iter__') and not isinstance(v,str) else v for v in [getattr(s,'default_value',None) for s in n.inputs])
    expected = defaults(node)
    blender_group_backend._copy_group_contents(src,dst,copy_role='backup')
    copied, = dst.nodes[:]
    assert defaults(copied) == expected
    assert [s.name for s in copied.inputs] == [s.name for s in node.inputs]
    assert [s.bl_idname for s in copied.outputs] == [s.bl_idname for s in node.outputs]
    if kind == 'Menu':
        assert [(i.name,i.description) for i in copied.enum_items] == [(i.name,i.description) for i in node.enum_items]
    elif kind == 'Capture':
        assert [(i.name,i.data_type) for i in copied.capture_items] == [(i.name,i.data_type) for i in node.capture_items]
    else:
        assert len(copied.index_switch_items) == count


@pytest.mark.parametrize('fault', ['label','type','missing'])
def test_checked_collection_endpoint_rejects_wrong_physical_layout(fault):
    """A same-type wrong item or missing slot cannot be rescued by a display-name lookup."""
    src = bpy.data.node_groups.new('NF_collection_fault_source', 'GeometryNodeTree')
    dst = bpy.data.node_groups.new('NF_collection_fault_dest', 'GeometryNodeTree')
    a = src.nodes.new('GeometryNodeCaptureAttribute')
    a.capture_items.new('BOOLEAN','Selection')
    a.capture_items.new('FLOAT','Other')
    b = dst.nodes.new('GeometryNodeCaptureAttribute')
    blender_group_backend._copy_node_properties(a,b)
    if fault == 'label':
        b.capture_items[0].name = 'Different'
    elif fault == 'type':
        b.capture_items[0].data_type = 'FLOAT'
    else:
        b.capture_items.remove(b.capture_items[0])
    with pytest.raises(CompileError, match='copy:'):
        blender_group_backend._resolve_standalone_copy_socket(a,a.inputs[2],b,output=False)


def test_collection_default_corruption_aborts_group_cutover_and_retries(monkeypatch):
    """A physical default changed after the local copy is caught before publication."""
    from pathlib import Path
    from NodeForge import packages
    from test_update_group import _evaluate_group_geometry_vertices as evaluate
    packages.install_package_directory(Path(__file__).resolve().parents[2] / 'packages' / 'nodes')
    source = '''from packages import nodes

def main():
    value = nodes.menu_switch(1.0, 2.0, 3.0, elements=["One","Two","Three"], menu="Three", data_type="FLOAT").output
    return {"geometry": point(vector(value,0.0,0.0))}
'''
    group = compiler.create_expression_group(source,'NF_collection_fault')
    assert evaluate(group,'before_fault') == [(3.,0.,0.)]
    groups = {g.as_pointer() for g in bpy.data.node_groups}
    original = blender_group_backend._copy_standalone_collection
    def corrupt(src,dst,**kwargs):
        original(src,dst,**kwargs)
        if src.bl_idname == 'GeometryNodeMenuSwitch' and src.inputs[3].default_value == 4.0:
            dst.inputs[3].default_value = 0.0
    changed = source.replace('2.0, 3.0','2.0, 4.0')
    with monkeypatch.context() as patch:
        patch.setattr(blender_group_backend,'_copy_standalone_collection',corrupt)
        with pytest.raises(CompileError,match='default readback differs'):
            compiler.update_expression_group(group,changed)
    assert evaluate(group,'after_fault') == [(3.,0.,0.)]
    assert groups == {g.as_pointer() for g in bpy.data.node_groups}
    compiler.update_expression_group(group,changed)
    assert evaluate(group,'after_retry') == [(4.,0.,0.)]


def test_menu_copy_preserves_connected_enum_selection_and_descriptions():
    """A linked Menu input retains its local enum and the physical incoming link."""
    src = bpy.data.node_groups.new('NF_menu_link_source','GeometryNodeTree')
    dst = bpy.data.node_groups.new('NF_menu_link_dest','GeometryNodeTree')
    src.interface.new_socket(name='Selector',in_out='INPUT',socket_type='NodeSocketMenu')
    entry = src.nodes.new('NodeGroupInput')
    menu = src.nodes.new('GeometryNodeMenuSwitch')
    menu.data_type = 'FLOAT'
    menu.enum_items.clear()
    menu.enum_items.new('One').description = 'First'
    menu.enum_items.new('Two').description = 'Second'
    menu.inputs[0].default_value = 'Two'
    src.links.new(entry.outputs[0],menu.inputs[0])
    blender_group_backend._copy_group_contents(src,dst,copy_role='backup')
    other = dst.nodes[menu.name]
    assert other.inputs[0].default_value == 'Two'
    assert [(i.name,i.description) for i in other.enum_items] == [('One','First'),('Two','Second')]
    link, = dst.links[:]
    assert link.from_node.bl_idname == 'NodeGroupInput' and link.to_socket == other.inputs[0]
    assert link.is_valid
