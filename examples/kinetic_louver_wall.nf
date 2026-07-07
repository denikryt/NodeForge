# Kinetic louver wall.
# Runtime grid dimensions create a facade of tilting zero-thickness blades driven by field math.
# No backing plate, no border frame, no blade size inputs.

from functions import grid_points

columns = input_int("Columns", default=18)
rows = input_int("Rows", default=9)
spacing_x = input_float("Spacing X", default=0.4)
spacing_y = input_float("Spacing Y", default=0.4)
max_tilt = input_float("Max Tilt", default=0.95)
phase = input_float("Phase", default=0.0)
wave_x = input_float("Wave X", default=3.2)
wave_y = input_float("Wave Y", default=1.8)

pts = grid_points(
    count=vector(columns, rows, 1),
    spacing=vector(spacing_x, spacing_y, 0),
    centered=True,
)

p = position()
wave = sin(p.x * wave_x + p.y * wave_y + phase)
cross = cos(p.x * wave_y - p.y * wave_x * 0.45 - phase * 0.7)
tilt = (wave * 0.75 + cross * 0.25) * max_tilt

blade_size = 0.4
blade_thickness = 0.0
blade = cube(size=vector(blade_size, blade_size, blade_thickness))

geo = instance_on_points(
    blade,
    pts,
    rotation=vector(tilt, 0, 0),
    realize=True,
)

geo = store_named_attribute(geo, "louver_tilt", tilt, domain="POINT", type="FLOAT")
geo = set_material(geo, "NodeForge Kinetic Louvers")
output("Geometry", geo)
