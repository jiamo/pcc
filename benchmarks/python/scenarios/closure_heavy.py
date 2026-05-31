"""Closure-heavy workload — function objects + cell access.

Builds many closures and calls them. Stresses the cell-allocation path
(``py_cell_new`` / ``py_cell_get``) and the heap-promotion logic that
fires when a local outlives its frame.
"""


def make_adder(n: int):
    def add(x: int) -> int:
        return x + n
    return add


def main() -> None:
    adders = []
    i: int = 0
    while i < 1000:
        adders.append(make_adder(i))
        i = i + 1

    total: int = 0
    j: int = 0
    while j < 1000:
        total = total + adders[j](j)   # adds j + j
        j = j + 1
    print(total)


if __name__ == "__main__":
    main()
