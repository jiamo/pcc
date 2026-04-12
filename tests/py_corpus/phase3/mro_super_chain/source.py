class A:
    def m(self) -> None:
        print("A.m")


class B(A):
    def m(self) -> None:
        print("B.m")
        super().m()


class C(A):
    def m(self) -> None:
        print("C.m")
        super().m()


class D(B, C):
    def m(self) -> None:
        print("D.m")
        super().m()


def main() -> None:
    d = D()
    d.m()


main()
