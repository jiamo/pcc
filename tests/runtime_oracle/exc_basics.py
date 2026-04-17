class MyError(Exception):
    pass


def boom():
    raise MyError("boom")


try:
    boom()
except MyError as e:
    print("caught:", str(e))

try:
    raise ValueError("bad value")
except ValueError as e:
    print("caught:", str(e))
except Exception as e:
    print("wrong branch")

captured = None
try:
    try:
        raise RuntimeError("inner")
    finally:
        print("finally-1")
except RuntimeError as e:
    captured = str(e)
print("outer caught:", captured)

try:
    xs = [1, 2, 3]
    print(xs[99])
except IndexError as e:
    print("index error:", str(e))
