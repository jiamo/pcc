def main() -> None:
    print(len(list()))
    print(len(list([1, 2, 3])))
    d: dict[str, int] = {"a": 1, "b": 2}
    print(len(list(d)))


main()
