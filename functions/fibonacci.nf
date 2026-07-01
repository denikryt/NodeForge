# Fibonacci sequence implemented in pure NodeForge DSL.
#
# N is a runtime input. The loop below compiles to a Blender Repeat Zone.
# F(0)=0, F(1)=1, F(n)=F(n-1)+F(n-2).
#
# Usage from another script:
#   n = input_int("N", default=8)
#   value = fibonacci(n)
#   output("Value", value)
#
# Or:
#   value = fibonacci(8)
#   output("Value", value)

n = input_int("N", default=8)

a = 0
b = 1

for i in range(n):
    next = a + b
    a = b
    b = next

output("Value", a)
