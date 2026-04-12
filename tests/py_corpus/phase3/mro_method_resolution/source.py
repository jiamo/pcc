class A:
    def m(self) -> None:
        print("A.m")


class B(A):
    def m(self) -> None:
        print("B.m")


class C(A):
    def m(self) -> None:
        print("C.m")


class D(B, C):
    pass


def main() -> None:
    d = D()
    d.m()


main()
