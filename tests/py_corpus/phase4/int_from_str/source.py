import builtins


def main() -> None:
    v = builtins.int("42")
    if v == 42:
        print("match")
    else:
        print("miss")


main()
