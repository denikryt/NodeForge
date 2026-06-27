# sierpinski_carpet
#
# Pure NodeForge DSL function.
# Builds a 2D Sierpinski-carpet style pattern by repeatedly applying
# copy_by_offsets(), which duplicates geometry into the eight outer cells
# of a 3x3 grid and skips the center.
#
# Inputs:
#   Geometry - source geometry
#   Steps    - number of iterations, runtime input
#   Scale    - per-iteration scale; use vector(1/3, 1/3, 1) for flat carpet
#              or vector(1/3, 1/3, 1/3) if you want Z to shrink too.

from functions import copy_by_offsets

geo = input_geometry("Geometry")
steps = input_int("Steps", default=2)
scale = input_vector("Scale", default=vector(1/3, 1/3, 1))

for i in range(steps):
    geo = copy_by_offsets(geo, scale=scale)

output("Geometry", geo)
