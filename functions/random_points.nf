from functions import layout_random

count = input_int("Count", default=16)
min_value = input_vector("Min", default=vector(-1, -1, -1))
max_value = input_vector("Max", default=vector(1, 1, 1))
seed = input_int("Seed", default=0)

count_value = count * 1.0
geometry = points(max(count_value, 0.0))
geometry = layout_random(geometry, min=min_value, max=max_value, seed=seed)
output("Geometry", geometry)
