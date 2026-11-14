def offset(amount: Float["Amount"] = 2.0, bias: Float["Bias"] = 3.0) -> Float:
    panel([bias, amount], name="Controls", collapsed=True)
    return amount * 2.0 + bias
