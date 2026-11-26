from packages import nodes

def entry(x: Float):
    c = nodes.cylinder(__inline__=False)
    a = nodes.capture_attribute(geometry=c.mesh, elements={"Two Words": x}, __inline__=False)
    m = nodes.menu_switch(x, a["Two Words"], data_type="FLOAT", elements=["A","Two Words"], menu="A", __inline__=False)
    i = nodes.index_switch(x, m.output, data_type="FLOAT", elements=2, index=1, __inline__=False)
    return {"mesh": a.geometry, "captured": a["Two Words"], "menu": m["Two Words"], "index": i}
