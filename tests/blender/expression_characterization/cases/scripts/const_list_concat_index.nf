a = input_float("A", default=1.0)
b = input_float("B", default=2.0)
c = input_float("C", default=3.0)
xs = [1, 2]
output("Result", [a, b, c][len(xs + [3]) - 1])
