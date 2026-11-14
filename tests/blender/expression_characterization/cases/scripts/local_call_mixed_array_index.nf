def identity(value: Float):
    return value

def nf_fixture(a: Float['A'], b: Float['B']):
    result = identity([a, b][0]) + b
    return result
