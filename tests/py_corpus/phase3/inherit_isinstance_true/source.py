class A:
    pass


class B(A):
    pass


def main() -> None:
    b = B()
    print(isinstance(b, A))
    print(isinstance(b, B))
    a = A()
    print(isinstance(a, A))
    print(isinstance(a, B))


main()
