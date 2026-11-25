from packages import extension_graphs

def entry(x: Float):
    a = shape(x, __inline__=False)
    b = shape(a, form="VECTOR", __inline__=False)
    return shape(a + b.x)
