v = input_vector("V", default=vector(1, 0, 0))
axis = input_vector("Axis", default=vector(0, 0, 1))
angle = input_float("Angle", default=0.0)

axis_n = normalize(axis)
cos_a = cos(angle)
sin_a = sin(angle)
term_a = v * cos_a
term_b = cross(axis_n, v) * sin_a
term_c = axis_n * (dot(axis_n, v) * (1.0 - cos_a))
output("Vector", term_a + term_b + term_c)
