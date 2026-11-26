from packages import nodes

def entry(v: Vector, g: Geometry):
    p = nodes.domain_size(geometry=g, component="POINTCLOUD", __inline__=False)
    i = nodes.domain_size(geometry=g, component="INSTANCES", __inline__=False)
    m = nodes.domain_size(geometry=g, component="MESH", __inline__=False)
    l = nodes.vector_math(vector=v, operation="LENGTH", __inline__=False)
    n = nodes.vector_math(vector=v, operation="NORMALIZE", __inline__=False)
    return {"points": p, "instances": i, "edges": m.edge_count, "length": l, "normal": n}
