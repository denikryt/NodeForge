base = cube(1.0)
raised = lift(base, vector(0, 0, 2))

def lift(geometry: Geometry, offset: Vector) -> Geometry:
    return transform(geometry, translation=offset)

def unused(geometry: Geometry) -> Geometry:
    return geometry

extra = cube(2.0)
