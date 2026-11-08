# Only functions reached by executable top-level statements are materialized.
base = cube(1.0)
through_chain = a(base)
direct_leaf = c(base)
extra = cube(2.0)

def a(geometry: Geometry) -> Geometry:
    return b(geometry)

def b(geometry: Geometry) -> Geometry:
    return c(geometry)

def c(geometry: Geometry) -> Geometry:
    return transform(geometry, translation=vector(0, 0, 1))

def unused_independent(geometry: Geometry) -> Geometry:
    return transform(geometry, translation=vector(0, 0, 99))

def unused_wrapper(geometry: Geometry) -> Geometry:
    return b(geometry)
