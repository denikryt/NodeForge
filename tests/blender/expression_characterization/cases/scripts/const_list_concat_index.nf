def nf_fixture(a: Float['A']=1.0, b: Float['B']=2.0, c: Float['C']=3.0):
    xs = [1, 2]
    return [a, b, c][len(xs + [3]) - 1]
