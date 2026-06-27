# Koch Curve example.
# Angle and Step are runtime inputs. The L-system iterations count is compile-time.

angle = input_float("Angle", default=60.0)
step = input_float("Step", default=0.08)

geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F+F--F+F"),
    ls_iterations(4),
    ls_angle(angle),
    ls_step(step),
)

output("Geometry", geo)
