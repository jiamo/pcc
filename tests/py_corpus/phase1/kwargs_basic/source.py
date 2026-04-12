def power(base: int, exp: int) -> int:
    r: int = 1
    i: int = 0
    while i < exp:
        r = r * base
        i = i + 1
    return r


def clamp(x: int, lo: int = 0, hi: int = 10) -> int:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def main() -> None:
    print(power(2, exp=8))
    print(power(base=3, exp=4))
    print(power(exp=5, base=2))
    print(clamp(5))
    print(clamp(-3))
    print(clamp(50))
    print(clamp(7, hi=6))
    print(clamp(7, lo=8))
    print(clamp(x=5, lo=1, hi=9))


main()
