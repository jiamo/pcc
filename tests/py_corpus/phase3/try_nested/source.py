def main() -> None:
    try:
        try:
            raise ValueError("inner")
        except ValueError:
            print("inner caught")
        print("after inner")
    except ValueError:
        print("outer caught")
    print("done")


main()
