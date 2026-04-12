def run(raise_it: bool) -> None:
    try:
        if raise_it:
            raise ValueError("boom")
        print("try body")
    except ValueError:
        print("caught")
    finally:
        print("finally")


def main() -> None:
    run(False)
    print("---")
    run(True)


main()
