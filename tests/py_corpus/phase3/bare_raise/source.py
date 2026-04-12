def main() -> None:
    try:
        try:
            raise ValueError("first")
        except ValueError:
            print("inner caught, re-raising")
            raise
    except ValueError as e:
        print("outer caught")
        print(str(e))


main()
