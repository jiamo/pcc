def run(which: int) -> None:
    try:
        if which == 1:
            raise ValueError("v")
        elif which == 2:
            raise KeyError("k")
        else:
            raise RuntimeError("r")
    except ValueError:
        print("value")
    except KeyError:
        print("key")
    except Exception:
        print("other")


def main() -> None:
    run(1)
    run(2)
    run(3)


main()
