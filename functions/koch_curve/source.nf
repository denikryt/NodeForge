# Koch curve function.
#
# Static mode:
#   steps = 3
#   geo = koch_curve(steps=steps, length=1)
#
# Runtime mode:
#   steps = input_int("Steps", default=2)
#   geo = koch_curve(steps=steps, max_steps=5, length=1)
#
# Runtime mode is implemented by this function's native Python helper:
# it prebuilds levels 0..max_steps and switches between them with the Steps input.

steps = 3

pts = [
    vector(-0.5, 0, 0),
    vector(0.5, 0, 0),
]

for i in range(steps):
    new_pts = []

    for j in range(len(pts) - 1):
        a = pts[j]
        b = pts[j + 1]
        v = b - a

        p0 = a
        p1 = a + v / 3
        p2 = a + v / 2 + vector(-v.y, v.x, 0) * 0.2886751346
        p3 = a + v * 2 / 3

        new_pts.append(p0)
        new_pts.append(p1)
        new_pts.append(p2)
        new_pts.append(p3)

    new_pts.append(pts[-1])
    pts = new_pts

geo = polyline(pts)
output("Geometry", geo)
