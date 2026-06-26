from functions import inverse_lerp, saturate

edge0 = input_float("Edge0", default=0.0)
edge1 = input_float("Edge1", default=1.0)
x = input_float("X", default=0.0)

t = saturate(inverse_lerp(edge0, edge1, x))
output("Value", t * t * (3.0 - 2.0 * t))
