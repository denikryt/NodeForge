# Fibonacci / sunflower spiral using runtime points, index(), and instance_on_points().
# Count changes the number of generated instances at runtime.

geo = input_geometry("Geometry")
count = input_int("Count", default=120)
scale = input_float("Scale", default=0.08)
instance_scale = input_float("Instance Scale", default=0.08)
turn = input_float("Turn", default=2.39996322973)

pts = points(count)

i = index()
a = i * turn
r = sqrt(i) * scale

pts = set_position(
    pts,
    vector(cos(a) * r, sin(a) * r, 0)
)

out = instance_on_points(
    geo,
    pts,
    scale=vector(instance_scale, instance_scale, instance_scale)
)

output("Geometry", out)
