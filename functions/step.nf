edge = input_float("Edge", default=0.0)
x = input_float("X", default=0.0)
output("Value", select(x >= edge, 0.0, 1.0))
