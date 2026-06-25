# Dragon Curve
#
# This function package has a native optimized backend in function.py.
# The backend generates mesh datablocks for Dragon Curve levels and builds
# a compact Geometry Nodes group that switches between those levels.
#
# Warning: there is no Python hard limit for max_steps, but high values become heavy:
# vertices = 2^max_steps + 1.
# max_steps=20 creates 1,048,577 vertices for the largest prebuilt level.
#
# Runtime example: Steps is exposed as a group input and can be changed in UI.

from functions import dragon_curve

steps = input_int("Steps", default=8)

geo = dragon_curve(
    steps=steps,
    max_steps=12,
    length=1
)

output("Geometry", geo)

# Static variant:
# geo = dragon_curve(steps=10, length=1)
# output("Geometry", geo)

# Runtime with a higher cap, use carefully:
# geo = dragon_curve(steps=steps, max_steps=20, length=1)
# output("Geometry", geo)
