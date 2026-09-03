flag = input_bool("Flag", default=True)
a = input_float("A", default=1.0)
b = input_float("B", default=2.0)
output("Result", a if flag else b)
