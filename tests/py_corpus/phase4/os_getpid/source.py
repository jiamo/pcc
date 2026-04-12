import os


def main() -> None:
    pid = os.getpid()
    if pid > 0:
        print("pid-ok")
    else:
        print("pid-bad")


main()
