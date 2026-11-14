def nf_fixture(a: Float['A']=1.0, b: Float['B']=2.0, c: Float['C']=3.0):
    xs = [0, 1]
    return [a, b, c][len(xs + range(1)) - 1]
