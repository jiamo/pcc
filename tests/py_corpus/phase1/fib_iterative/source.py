def fib(n: int) -> int:
    if n < 2:
        return n
    a: int = 0
    b: int = 1
    i: int = 2
    while i <= n:
        c: int = a + b
        a = b
        b = c
        i = i + 1
    return b


def main() -> None:
    print(fib(0))
    print(fib(1))
    print(fib(5))
    print(fib(10))
    print(fib(20))
    print(fib(30))


main()
