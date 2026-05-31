"""Typed integer loop — exercises tagged-int fast paths in
``py_int_floordiv`` / ``py_int_mod`` / ``py_int_add``.

Should run within ~5% of CPython after RM-P5 lands. Pre-RM-P5 this
was 75x slower.
"""


def main() -> None:
    n: int = 20_000_000
    acc: int = 0
    i: int = 0
    while i < n:
        acc = acc + i // 7 + i % 13
        i = i + 1
    print(acc)


if __name__ == "__main__":
    main()
