from packages import extension_graphs

def build(value: Float) -> Float:
    first = scale(value)
    return scale(first, __inline__=False)

result = build(3.0)
