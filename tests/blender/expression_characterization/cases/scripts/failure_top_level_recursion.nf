def first(x: Float) -> Float:
    return second(x)

def second(x: Float) -> Float:
    return first(x)

result = first(1.0)
