geometry = input_geometry("Geometry")
min_value = input_vector("Min", default=vector(-1, -1, -1))
max_value = input_vector("Max", default=vector(1, 1, 1))
seed = input_int("Seed", default=0)

pos = random_value(min_value, max_value, seed=seed, id=index())
geometry = set_position(geometry, pos)
output("Geometry", geometry)
