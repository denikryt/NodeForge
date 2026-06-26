a = input_vector("A", default=vector(1, 0, 0))
b = input_vector("B", default=vector(0, 1, 0))
output("Value", acos(clamp(dot(normalize(a), normalize(b)), -1.0, 1.0)))
