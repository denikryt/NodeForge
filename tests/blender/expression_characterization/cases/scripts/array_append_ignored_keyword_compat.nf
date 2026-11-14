def nf_fixture(a: Float['A']=1.0):
    items = [a]
    items.append(a, ignored=missing)
    return items[1]
