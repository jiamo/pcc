def swap(x: int, y: int) -> tuple[int, int]:
    return y, x


def divmod_pair(a: int, b: int) -> tuple[int, int]:
    return a // b, a % b


def main() -> None:
    a, b = swap(7, 3)
    print(a)
    print(b)
    q, r = divmod_pair(17, 5)
    print(q)
    print(r)


main()
