from functions import layout_grid

count = input_vector("Count", default=vector(1, 1, 1))
spacing = input_vector("Spacing", default=vector(1, 1, 1))
centered = input_bool("Centered", default=False)

total = max(count.x, 0.0) * max(count.y, 0.0) * max(count.z, 0.0)
geometry = points(total)
geometry = layout_grid(geometry, count=count, spacing=spacing, centered=centered)
output("Geometry", geometry)
