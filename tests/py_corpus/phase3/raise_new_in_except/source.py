def main() -> None:
    try:
        try:
            raise ValueError("first")
        except ValueError:
            print("handling first")
            raise RuntimeError("second")
    except RuntimeError as e:
        print("caught second")
        print(str(e))


main()
