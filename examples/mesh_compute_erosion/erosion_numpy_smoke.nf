# Minimal self-contained Mesh Compute E2E check (NodeForge Math not required).
from local import erosion
n = input_int("Resolution", default=8)
steps = input_int("Steps", default=2)
size = input_float("Size", default=10.0)
geo = node("GeometryNodeMeshGrid", inputs={"Size X": size, "Size Y": size, "Vertices X": n, "Vertices Y": n}, output="Mesh", typ=Geometry)
initial = 2.0 + position().x * 0.07 + position().y * 0.11
geo = store_named_attribute(geo, "height", initial, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "initial_height", initial, domain="POINT", type="FLOAT")
geo = erosion(geo, steps=steps, n=n, size=size)
height = node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float)
geo = set_position(geo, vector(position().x, position().y, height))
output("Geometry", geo)
