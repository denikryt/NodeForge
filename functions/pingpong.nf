x = input_float("X", default=0.0)
length = input_float("Length", default=1.0)

double_length = length * 2.0
wrapped = mod(mod(x, double_length) + double_length, double_length)
output("Value", length - abs(wrapped - length))
