def leaf(x: Float) -> Float:
    return x + 1.0

def middle(x: Float) -> Float:
    return leaf(x) * 2.0

def entry(x: Float) -> Float:
    return middle(x)

def independent(x: Float) -> Float:
    return x - 1.0
