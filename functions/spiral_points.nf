from functions import layout_spiral

count = input_int("Count", default=16)
radius = input_float("Radius", default=1.0)
turns = input_float("Turns", default=1.0)
height = input_float("Height", default=0.0)
start_radius = input_float("Start Radius", default=0.0)
start_angle = input_float("Start Angle", default=0.0)

count_value = count * 1.0
geometry = points(max(count_value, 0.0))
geometry = layout_spiral(
    geometry,
    count=count,
    radius=radius,
    turns=turns,
    height=height,
    start_radius=start_radius,
    start_angle=start_angle,
)
output("Geometry", geometry)
