class A:
    pass


class B(A):
    pass


class C(A):
    pass


class D(B, C):
    pass


def main() -> None:
    for cls in D.__mro__:
        print(cls.__name__)


main()
