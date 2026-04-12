def classify(x: int) -> int:
    if x < 0:
        return -1
    elif x == 0:
        return 0
    elif x < 10:
        return 1
    elif x < 100:
        return 2
    else:
        return 3


def main() -> None:
    print(classify(-5))
    print(classify(0))
    print(classify(7))
    print(classify(50))
    print(classify(500))
    print(classify(9))
    print(classify(99))


main()
