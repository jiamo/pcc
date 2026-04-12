def main() -> None:
    try:
        try:
            raise ValueError("root cause")
        except ValueError as e:
            raise RuntimeError("wrapped") from e
    except RuntimeError as outer:
        print("caught outer")
        print(str(outer))
        cause = outer.__cause__
        if cause is None:
            print("no cause")
        else:
            print(type(cause).__name__)
            print(str(cause))


main()
