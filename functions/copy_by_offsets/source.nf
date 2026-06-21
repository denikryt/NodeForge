# copy_by_offsets
#
# Pure NodeForge DSL function.
#
# Duplicates input Geometry into the 8 cells around the center of a 3x3 grid.
# This is the Sierpinski-carpet style offset pattern.
#
# Inputs:
#   Geometry - source geometry
#   Scale    - per-copy scale, default vector(1/3, 1/3, 1)

geo = input_geometry("Geometry")
scale = input_vector("Scale", default=vector(1/3, 1/3, 1))

geo = join([
    transform(geo, translation=vector(-2/3, -2/3, 0), scale=scale),
    transform(geo, translation=vector(0, -2/3, 0), scale=scale),
    transform(geo, translation=vector(2/3, -2/3, 0), scale=scale),

    transform(geo, translation=vector(-2/3, 0, 0), scale=scale),
    transform(geo, translation=vector(2/3, 0, 0), scale=scale),

    transform(geo, translation=vector(-2/3, 2/3, 0), scale=scale),
    transform(geo, translation=vector(0, 2/3, 0), scale=scale),
    transform(geo, translation=vector(2/3, 2/3, 0), scale=scale),
])

output("Geometry", geo)
