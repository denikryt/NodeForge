from packages import extension_graphs

def entry(x: Float):
    direct = shape_state(x, __inline__=False)
    named = shape_state(direct, form="MULTI", __inline__=False)
    single = shape_state(named.scaled, form="ONE", __inline__=True)
    return {"a": named.scaled, "b": named["scaled"], "c": named.original, "single": single.value}
