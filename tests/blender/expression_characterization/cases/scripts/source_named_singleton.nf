def make(value: Float):
    return {"value": value * 2}

def entry(x: Float):
    r = make(x)
    return {"dot": r.value, "key": r["value"]}
