a = input_float("A", default=1.0)
b = input_float("B", default=2.0)
c = input_float("C", default=3.0)
xs = [0, 1]
output("Result", [a, b, c][len(xs + range(1)) - 1])
