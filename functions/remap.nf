from functions import inverse_lerp

x = input_float("X", default=0.0)
in_min = input_float("In Min", default=0.0)
in_max = input_float("In Max", default=1.0)
out_min = input_float("Out Min", default=0.0)
out_max = input_float("Out Max", default=1.0)

t = inverse_lerp(in_min, in_max, x)
output("Value", out_min + t * (out_max - out_min))
