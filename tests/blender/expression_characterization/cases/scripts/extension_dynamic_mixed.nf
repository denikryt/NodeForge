from packages import extension_graphs
result = mixed(2.0, __inline__=False)
scalar = result.scalar
vector_value = result.vector
result = scalar + vector_value.x
