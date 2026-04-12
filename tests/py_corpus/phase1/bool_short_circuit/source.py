def trace_true() -> bool:
    print(1)
    return True


def trace_false() -> bool:
    print(0)
    return False


def and_test() -> None:
    if trace_false() and trace_true():
        print(100)
    else:
        print(200)


def or_test() -> None:
    if trace_true() or trace_false():
        print(300)
    else:
        print(400)


def main() -> None:
    print(True and True)
    print(True and False)
    print(False and True)
    print(True or False)
    print(False or False)
    and_test()
    or_test()


main()
