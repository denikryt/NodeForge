v = input_vector("V", default=vector(1, 0, 0))
angle = input_float("Angle", default=0.0)

cos_a = cos(angle)
sin_a = sin(angle)
out_x = v.x * cos_a - v.y * sin_a
out_y = v.x * sin_a + v.y * cos_a
output("Vector", vector(out_x, out_y, v.z))
