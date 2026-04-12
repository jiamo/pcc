class A:
    def who(self) -> None:
        print("A")


class B(A):
    pass


class C(B):
    pass


def main() -> None:
    a = A()
    b = B()
    c = C()
    a.who()
    b.who()
    c.who()


main()
