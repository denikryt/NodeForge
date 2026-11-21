from packages import extension_graphs
geometry = cube(1.0)
nested = capture(geometry, __inline__=False)
result = capture(nested)
