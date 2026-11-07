def nf_fixture(flag: Bool['Flag']=True, a: Float['A']=1.0, b: Float['B']=2.0, c: Float['C']=3.0):
    return (a if flag else b) + c
