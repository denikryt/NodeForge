# Field-driven tiled wave.
# Shows grid point layouts, vector length, smoothstep falloff, per-instance scale, and trigonometric fields.

from functions import grid_points, smoothstep

count_x = input_int("Count X", default=18)
count_y = input_int("Count Y", default=18)
spacing = input_float("Spacing", default=0.28)
amplitude = input_float("Amplitude", default=0.8)
phase = input_float("Phase", default=0.0)

pts = grid_points(
    count=vector(count_x, count_y, 1),
    spacing=vector(spacing, spacing, 0),
    centered=True,
)

pos = position()
d = length(vector(pos.x, pos.y, 0))
wave = sin(d * 5.0 - phase) * amplitude
falloff = 1.0 - smoothstep(0.0, 3.6, d)
z = wave * falloff
pts = set_position(pts, pos + vector(0, 0, z))

tile = cube(size=1.0)
scale_z = clamp(0.12 + abs(z) * 0.55, 0.08, 0.65)
scale_xy = spacing * 0.65
geo = instance_on_points(tile, pts, scale=vector(scale_xy, scale_xy, scale_z), realize=True)
geo = set_material(geo, "NodeForge Ripple Tiles")

output("Geometry", geo)
