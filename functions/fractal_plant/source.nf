angle = input_float("Angle", default=25.0)
step = input_float("Step", default=0.04)

geo = ls_system(
    ls_axiom("X"),
    ls_rule("X", "F+[[X]-X]-F[-FX]+X"),
    ls_rule("F", "FF"),
    ls_iterations(5),
    ls_angle(angle),
    ls_step(step),
)

geo = transform(geo, rotation=vector(0, 0, radians(90)))
output("Geometry", geo)