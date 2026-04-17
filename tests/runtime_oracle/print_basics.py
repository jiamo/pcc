import sys

print("plain")
print("int:", 42)
print("float:", 3.14)
print("list:", [1, 2, 3])
print("dict:", {"a": 1})
print("tuple:", (1, 2))
print("joined", "pieces", "here")
print("sep=", "a", "b", "c", sep="-")
print("end=", "x", end="!\n")
print("stderr hi", file=sys.stderr)
