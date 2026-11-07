def nf_fixture(a: Float['A']=1.0, b: Float['B']=2.0, c: Float['C']=3.0, d: Float['D']=4.0):
    return [a + b, c * d][0]
