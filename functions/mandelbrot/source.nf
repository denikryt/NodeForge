# Mandelbrot height field.
#
# The fractal algorithm is written in NodeForge DSL. The package-local Python
# helper only creates the shader material that displays the generated color
# attribute; it is not a global DSL built-in.

width = input_int("Width", default=160)
height = input_int("Height", default=100)
max_iter = input_int("Max Iter", default=48)
z_scale = input_float("Z Scale", default=0.025)

geo = grid(width, height)

uv = grid_uv()
cx = map_range(uv.x, 0, 1, -2.5, 1.0)
cy = map_range(uv.y, 0, 1, -1.5, 1.5)

zx = 0
zy = 0
escaped = False
iteration = 0

for i in runtime_range(max_iter):
    zx_next = zx * zx - zy * zy + cx
    zy_next = 2 * zx * zy + cy
    mag2 = zx_next * zx_next + zy_next * zy_next

    if not escaped:
        zx = zx_next
        zy = zy_next
        iteration = i
        escaped = mag2 > 4

value = select(escaped, 0, iteration / max_iter)
color = vector(value, value * value, 1 - value)

geo = set_position(
    geo,
    vector(cx, cy, value * max_iter * z_scale)
)

geo = store_named_attribute(geo, "mandelbrot_color", color, type="COLOR")
geo = apply_mandelbrot_material(geo, "mandelbrot_color")

output("Geometry", geo)
