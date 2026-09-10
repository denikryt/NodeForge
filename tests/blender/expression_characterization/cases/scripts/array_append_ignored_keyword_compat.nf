a = input_float("A", default=1.0)
items = [a]
items.append(a, ignored=missing)
output("Result", items[1])
