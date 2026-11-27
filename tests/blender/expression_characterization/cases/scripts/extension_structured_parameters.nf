from packages import extension_graphs

def entry(x: Float, y: Float):
    labels = ["total"]
    r = structured({"x": x, "weight": 0.75}, [y, 2.0], labels=labels, __inline__=False)
    a = structured({"x": x, "weight": 0.5}, [y, 3.0], labels=[], __inline__=True)
    return {"named": r.total, "reused": r["total"], "direct": a}
