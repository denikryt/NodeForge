from helpers import *




def test_representative_compile_fixtures():
    fixtures = {'inputs': '\na = input_float("A", default=0.25)\nb = input_int("B", default=3)\nc = input_bool("C", default=True)\nv = input_vector("V", default=(1,2,3))\noutput("Float", a + b)\noutput("Bool", c)\noutput("Vec", v)\n', 'math_vector': '\na = inverse_lerp(0, 10, 5)\nb = remap(a, 0, 1, -1, 1)\nc = saturate(b + 2)\nd = step(0.5, c)\ne = smoothstep(0, 1, c)\nf = smootherstep(0, 1, c)\ng = pingpong(-0.25, 1)\nh = wrap(-1, 0, 2)\nv = rotate2d(polar(2, 1.57079632679), 1.57079632679)\nw = rotate_around_axis(vector(1,0,0), vector(0,0,1), 1.57079632679)\nang = angle_between(vector(1,0,0), vector(0,1,0))\noutput("Scalar", a+b+c+d+e+f+g+h+ang)\noutput("Vector", v + w)\n', 'geometry_fields': '\ngeo = grid(4, 3)\nuv = grid_uv()\nheight = smoothstep(0, 1, uv.x)\npos = vector(uv.x, uv.y, height)\nstore("h", height, domain="FACE")\nset_position(pos)\n', 'runtime_scalar': '\nx = 0\nfor i in runtime_range(5):\n    if x < 3:\n        x = x + 1\n    else:\n        x = x\noutput("x", x)\n', 'runtime_vector_bool': '\nv = vector(1,0,0)\nflag = True\nfor i in runtime_range(3):\n    if flag:\n        v = rotate_around_axis(v, vector(0,0,1), 0.1)\n        flag = False\n    else:\n        v = v\n        flag = flag\noutput("v", v)\noutput("flag", flag)\n', 'runtime_geometry': '\ngeo = cube(1)\nfor i in range(3):\n    geo = transform(geo, translation=vector(0.1, 0, 0))\noutput("Geometry", geo)\n', 'local_function': '\ndef scale_add(a, b):\n    c = a * 2 + b\n    return c\nx = scale_add(1, input_float("B"))\noutput("x", x)\n'}
    for name, source in fixtures.items():
        compile_group(source, 'NFTest_' + name)
    print('COMPILE_FIXTURES_OK')
