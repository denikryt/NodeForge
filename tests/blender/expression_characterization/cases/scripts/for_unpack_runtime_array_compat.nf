def nf_fixture(a: Float['A']=1.0):
    pairs = [[a, a]]
    for x, y in pairs:
        result = x + y
    return result
