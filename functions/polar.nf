radius = input_float("Radius", default=1.0)
angle = input_float("Angle", default=0.0)
output("Vector", vector(cos(angle) * radius, sin(angle) * radius, 0.0))
