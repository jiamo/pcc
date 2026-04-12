from pcc.extern import extern, c_int


getpid = extern("getpid", (), c_int)


def main() -> None:
    pid: int = getpid()
    if pid > 0:
        print("pid-ok")
    else:
        print("pid-bad")


main()
