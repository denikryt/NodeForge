def bump(x: Float) -> Float:
    return x + 1.0

def stretch(x: Float) -> Float:
    return bump(x) * 2.0

# Imported modules must not execute executable top-level statements.
cube(9.0)
