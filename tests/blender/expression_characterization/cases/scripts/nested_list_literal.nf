def nf_fixture(a: Float['A']=1.0, b: Float['B']=2.0, c: Float['C']=3.0):
    items = [[a, b], c]
    return items[0][1]
