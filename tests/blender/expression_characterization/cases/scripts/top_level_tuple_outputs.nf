def split(x: Float):
    twice = x * 2.0
    plus_one = x + 1.0
    return twice, plus_one

left, right = split(4.0)
result = left + right
