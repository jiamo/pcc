def main() -> None:
    s: set = set()
    s.add(1)
    s.add(2)
    s.remove(1)
    print(len(s))
    print(1 in s)
    print(2 in s)


main()
