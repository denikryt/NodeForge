count = input_int("Count", default=16)
radius = input_float("Radius", default=1.0)
start_angle = input_float("Start Angle", default=0.0)
end_angle = input_float("End Angle", default=tau)
include_endpoint = input_bool("Include Endpoint", default=False)

count_value = count * 1.0
pts = points(max(count_value, 0.0))

raw_denominator = select(include_endpoint, count_value - 1.0, count_value)
denominator = max(raw_denominator, 1.0)

t = index() / denominator
angle = start_angle + (end_angle - start_angle) * t
pos = vector(cos(angle) * radius, sin(angle) * radius, 0)

pts = set_position(pts, pos)
output("Geometry", pts)
