"""Dict-heavy workload — string keys, repeated insert/lookup.

Stresses ``py_dict_setitem`` / ``py_dict_getitem`` and the string
hash path. Pcc dict is open-addressed; this measures how close that
implementation runs to CPython's combined-table dict.
"""


def main() -> None:
    d = {}
    n: int = 100_000
    i: int = 0
    while i < n:
        key: str = "k" + str(i % 1000)   # 1000 distinct keys
        d[key] = i
        i = i + 1

    # Re-read every key.
    total: int = 0
    j: int = 0
    while j < 1000:
        total = total + d["k" + str(j)]
        j = j + 1
    print(total)


if __name__ == "__main__":
    main()
