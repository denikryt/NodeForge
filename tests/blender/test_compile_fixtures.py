from helpers import *




def test_representative_compile_fixtures():
    fixtures = {
        "inputs": 'def nf_fixture(a: Float["A"] = 0.25, b: Int["B"] = 3, c: Bool["C"] = True, v: Vector["V"] = (1, 2, 3)):\n    result = a + b\n    flag = c\n    vec = v\n    return (result, flag, vec)\n',
        "runtime_scalar": 'def nf_fixture():\n    x = 0\n    for i in repeat_range(5):\n        if x < 3:\n            x = x + 1\n        else:\n            x = x\n    return x\n',
        "runtime_geometry": 'def nf_fixture():\n    geo = cube(1)\n    for i in repeat_range(3):\n        geo = transform(geo, translation=vector(0.1, 0, 0))\n    return geo\n',
        "local_function": 'def scale_add(a: Float, b: Float) -> Float:\n    return a * 2 + b\n\ndef nf_fixture(b: Float["B"]):\n    return scale_add(1.0, b)\n',
    }
    for name, source in fixtures.items():
        if name == 'local_function':
            tree = bpy.data.node_groups.new('NFTest_' + name, 'GeometryNodeTree')
            compiler.compose_source_initial(source + '\nresult = nf_fixture(2.0)\n', tree)
        else:
            compile_group(source, 'NFTest_' + name)
    print('COMPILE_FIXTURES_OK')
