from local import shapes

def inner(x: Float, __inline__=True):
    return {"left": x+1, "right": x+2}

def entry(x: Float):
    r = shapes.pair(x)
    alias = r
    r = inner(x)
    return {"first": alias.left, "second": alias["right"], "reuse": alias["left"], "new": r.right}
