def identity(value):
    return value

a = input_float("A")
b = input_float("B")
result = identity([a, b][0]) + b
output("Result", result)
