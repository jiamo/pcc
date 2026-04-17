class MyError(Exception):
    pass


def boom():
    raise MyError("boom")


try:
    boom()
except MyError as e:
    print(str(e))
