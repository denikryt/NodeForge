# Dragon Curve example.
# Angle and Step are runtime inputs. The L-system iterations count is compile-time.

angle = input_float("Angle", default=90.0)
step = input_float("Step", default=0.04)

geo = ls_system(
    ls_axiom("FX"),
    ls_rule("X", "X+YF+"),
    ls_rule("Y", "-FX-Y"),
    ls_iterations(10),
    ls_angle(angle),
    ls_step(step),
)

output("Geometry", geo)
