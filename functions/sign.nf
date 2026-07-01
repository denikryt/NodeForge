x = input_float("X", default=0.0)
positive_or_zero = select(x > 0.0, 1.0, 0.0)
output("Value", select(x < 0.0, -1.0, positive_or_zero))
