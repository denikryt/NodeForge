from packages import extension_graphs

def entry(x: Float):
    one = named_one(x, __inline__=False)
    two = named_two(x, __inline__=False)
    direct = scale(x, __inline__=False)
    positional = positional_one(x, __inline__=False)
    return {"one": one.value, "scaled": two.scaled, "original": two["original"], "direct": direct, "positional": positional[0]}
