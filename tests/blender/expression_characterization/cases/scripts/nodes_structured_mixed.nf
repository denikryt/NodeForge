from packages import nodes

def entry(x: Float):
    geo = nodes.mesh_line(count=3)
    r = nodes.capture_attribute(geometry=geo, elements={"X, y": x, "weight": 0.75, "rest": vector(1, 2, 3)}, __inline__=False)
    options = ["One, two", "Third"]
    m = nodes.menu_switch(r.weight, r["X, y"], elements=options, menu="One, two", data_type="FLOAT", __inline__=True)
    return {"geometry": r.geometry, "captured": r["X, y"], "rest": r.rest, "value": m.output, "choice": m["One, two"]}
