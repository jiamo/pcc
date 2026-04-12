def is_even(n: int) -> bool:
    if n == 0:
        return True
    return is_odd(n - 1)


def is_odd(n: int) -> bool:
    if n == 0:
        return False
    return is_even(n - 1)


def main() -> None:
    print(is_even(0))
    print(is_odd(0))
    print(is_even(7))
    print(is_odd(7))
    print(is_even(10))
    print(is_odd(10))


main()
