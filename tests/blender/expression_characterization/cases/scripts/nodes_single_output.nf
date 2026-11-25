from packages import nodes

def entry():
    p = nodes.position(__inline__=False)
    i = nodes.index()
    c = nodes.curve_line(start=p, end=p + vector(1, 0, 0))
    return {"curve": c, "index": i, "x": p.x}
