edge = input_float("Edge", default=0.0)
x = input_float("X", default=0.0)
output("Value", select(x >= edge, 1.0, 0.0))
