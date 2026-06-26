geometry = input_geometry("Geometry")
count = input_int("Count", default=16)
radius = input_float("Radius", default=1.0)
turns = input_float("Turns", default=1.0)
height = input_float("Height", default=0.0)
start_radius = input_float("Start Radius", default=0.0)
start_angle = input_float("Start Angle", default=0.0)

count_value = count * 1.0
denominator = max(count_value - 1.0, 1.0)
t = index() / denominator

r = start_radius + (radius - start_radius) * t
angle = start_angle + tau * turns * t
pos = vector(cos(angle) * r, sin(angle) * r, height * t)

geometry = set_position(geometry, pos)
output("Geometry", geometry)
