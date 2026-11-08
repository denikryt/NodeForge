from local import operations
first = operations.step(1.0)
second = operations.step(2.0)
independent = operations.step(3.0, __unique__=True)
