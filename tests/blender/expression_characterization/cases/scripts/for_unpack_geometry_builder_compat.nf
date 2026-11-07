def nf_fixture():
    builder = geometry_builder()
    for x, y in [[1.0, 2.0]]:
        builder.add(cube(x))
    return builder.geometry
