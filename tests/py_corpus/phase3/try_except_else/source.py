def run(raise_it: bool) -> None:
    try:
        if raise_it:
            raise ValueError("boom")
        print("try body ok")
    except ValueError:
        print("caught")
    else:
        print("else ran")
    print("after")


def main() -> None:
    run(False)
    print("---")
    run(True)


main()
