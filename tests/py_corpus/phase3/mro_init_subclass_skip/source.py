class A:
    def __init__(self) -> None:
        print("A init")


class B(A):
    def __init__(self) -> None:
        super().__init__()
        print("B init")


class C(B):
    def __init__(self) -> None:
        super().__init__()
        print("C init")


def main() -> None:
    c = C()
    print("done")


main()
