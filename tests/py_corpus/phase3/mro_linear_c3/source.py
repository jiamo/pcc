class A:
    pass


class B(A):
    pass


class C(B):
    pass


class D(C):
    pass


def main() -> None:
    for cls in D.__mro__:
        print(cls.__name__)


main()
