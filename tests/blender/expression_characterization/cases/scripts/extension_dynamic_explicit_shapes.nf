from packages import extension_graphs

def entry(x: Float):
    direct = shape(x, __inline__=False)
    one = shape(direct, form="ONE", __inline__=False)
    multi = shape(one.value, form="MULTI", __inline__=False)
    a = shape(multi.scaled, __inline__=True)
    b = shape(a, form="ONE", __inline__=True)
    c = shape(b["value"], form="MULTI", __inline__=True)
    return {"first": multi.scaled, "reuse": multi["scaled"], "other": multi.original, "inline": c.scaled, "original": c.original}
