x = input_float("X", default=0.0)
min_value = input_float("Min", default=0.0)
max_value = input_float("Max", default=1.0)

size = max_value - min_value
output("Value", min_value + mod(mod(x - min_value, size) + size, size))
