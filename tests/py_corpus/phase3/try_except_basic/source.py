def main() -> None:
    try:
        raise ValueError("bad value")
        print("unreachable")
    except ValueError:
        print("caught")
    print("after")


main()
