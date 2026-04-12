import time


def main() -> None:
    t = time.monotonic()
    if t > 0.0:
        print("ok")
    else:
        print("bad")


main()
