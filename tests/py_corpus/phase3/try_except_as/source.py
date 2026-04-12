def main() -> None:
    try:
        raise ValueError("oops")
    except Exception as e:
        print("caught")
        print(str(e))
    print("after")


main()
