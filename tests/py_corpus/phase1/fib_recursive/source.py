def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def main() -> None:
    print(fib(0))
    print(fib(1))
    print(fib(5))
    print(fib(10))
    print(fib(15))


main()
