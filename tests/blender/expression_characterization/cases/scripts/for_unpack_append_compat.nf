def nf_fixture():
    items = []
    for x, y in [[1.0, 2.0]]:
        items.append(x + y)
    return items[0]
