d = {"a": 1, "b": 2, "c": 3}
print(d["a"], d["b"], d["c"])
print(len(d))
d["d"] = 4
print(sorted(d.keys()))
print(sorted(d.values()))
print(sorted(d.items()))
print("a" in d, "z" in d)
del d["b"]
print(sorted(d.items()))
print(d.get("x", -1))
