def deferred(value):
    return locals()


print("imported")
try:
    deferred("1")
except NotImplementedError as exc:
    print(str(exc))
