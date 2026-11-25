from packages import extension_graphs
vector_value = choose(2.0, form="VECTOR", __inline__=False).value
scalar = choose(1.0, form="FLOAT", __inline__=False).value
result = vector_value.x + scalar
