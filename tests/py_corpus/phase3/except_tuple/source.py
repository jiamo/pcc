def run(which: int) -> None:
    try:
        if which == 1:
            raise ValueError("v")
        elif which == 2:
            raise TypeError("t")
        else:
            raise KeyError("k")
    except (ValueError, TypeError):
        print("numeric")
    except KeyError:
        print("key")


def main() -> None:
    run(1)
    run(2)
    run(3)


main()
