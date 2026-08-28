from helpers import *




def test_representative_compile_fixtures():
    fixtures = {
        "inputs": '\na = input_float("A", default=0.25)\nb = input_int("B", default=3)\nc = input_bool("C", default=True)\nv = input_vector("V", default=(1,2,3))\noutput("Float", a + b)\noutput("Bool", c)\noutput("Vec", v)\n',
        "runtime_scalar": '\nx = 0\nfor i in repeat_range(5):\n    if x < 3:\n        x = x + 1\n    else:\n        x = x\noutput("x", x)\n',
        "runtime_geometry": '\ngeo = cube(1)\nfor i in repeat_range(3):\n    geo = transform(geo, translation=vector(0.1, 0, 0))\noutput("Geometry", geo)\n',
        "local_function": '\ndef scale_add(a, b):\n    c = a * 2 + b\n    return c\nx = scale_add(1, input_float("B"))\noutput("x", x)\n',
    }
    for name, source in fixtures.items():
        compile_group(source, 'NFTest_' + name)
    print('COMPILE_FIXTURES_OK')
