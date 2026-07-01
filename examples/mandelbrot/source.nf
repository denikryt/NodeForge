# Mandelbrot color field.
#
# The fractal algorithm is written in NodeForge DSL. The package-local Python
# helper only creates the shader material that displays the generated face color
# attribute; it is not a global DSL built-in.

resolution = input_int("Resolution", default=300)
max_iter = input_int("Max Iter", default=48)

# Resolution is the number of vertical cells. The horizontal cell count follows
# the Mandelbrot viewport aspect, and the X extent is adjusted to keep cells square.
height = resolution + 1
width = round(resolution * 3.5 / 3.0) + 1
cell_size = 3.0 / resolution

geo = grid(width, height)

uv = grid_uv()
cx = map_range(uv.x, 0, 1, -2.5, -2.5 + (width - 1) * cell_size)
cy = map_range(uv.y, 0, 1, -1.5, -1.5 + resolution * cell_size)

zx = 0
zy = 0
escaped = False
iteration = 0

for i in repeat_range(max_iter):
    zx_next = zx * zx - zy * zy + cx
    zy_next = 2 * zx * zy + cy
    mag2 = zx_next * zx_next + zy_next * zy_next

    if not escaped:
        zx = zx_next
        zy = zy_next
        iteration = i
        escaped = mag2 > 4

value = select(escaped, iteration / max_iter, 0)
color = vector(value, value * value, 1 - value)

geo = set_position(
    geo,
    vector(cx, cy, 0)
)

geo = store_named_attribute(geo, "mandelbrot_color", color, domain="FACE", type="COLOR")
geo = apply_mandelbrot_material(geo, "mandelbrot_color")

output("Geometry", geo)
