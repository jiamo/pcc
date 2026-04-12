class Squares:
    def __getitem__(self, k: int) -> int:
        return k * k


def main() -> None:
    s = Squares()
    print(s[0])
    print(s[1])
    print(s[2])
    print(s[5])
    print(s[10])


main()
