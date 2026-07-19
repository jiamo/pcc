"""Pinned M3 value-array kernel and Python-semantics slow-path oracle."""

from typing import Any

import pcc


@pcc.valueclass
class Sample:
    x: float
    y: float


def make_samples() -> pcc.array[Sample, 2]:
    return pcc.array[Sample, 2](Sample(0.125, 0.5), Sample(0.25, 0.75))


def hot(values: pcc.array[Sample, 2], rounds: int) -> float:
    total: float = 0.25
    i: int = 0
    while i < rounds:
        left = values[0]
        right = values[1]
        total = (total + left.x) * right.y - left.y
        total = (total + right.x) * left.y - right.y
        total = (total + left.x) * right.y - left.y
        total = (total + right.x) * left.y - right.y
        total = (total + left.x) * right.y - left.y
        total = (total + right.x) * left.y - right.y
        total = (total + left.x) * right.y - left.y
        total = (total + right.x) * left.y - right.y
        total = (total + left.x) * right.y - left.y
        total = (total + right.x) * left.y - right.y
        total = (total + left.x) * right.y - left.y
        total = (total + right.x) * left.y - right.y
        total = (total + left.x) * right.y - left.y
        total = (total + right.x) * left.y - right.y
        total = (total + left.x) * right.y - left.y
        total = (total + right.x) * left.y - right.y
        i = i + 1
    return total


def checked(values: pcc.array[Sample, 2], index: int) -> Sample:
    return values[index]


def escape(values: pcc.array[Sample, 2], index: int) -> Any:
    return values[index]


def main() -> None:
    print(hot(make_samples(), 1_000_000))  # PCC_M3_BENCHMARK_ROUNDS
    print(checked(make_samples(), -1).x)
    try:
        checked(make_samples(), 2)
    except IndexError:
        print("index-error")
    try:
        checked(make_samples(), 1 << 100)
    except OverflowError:
        print("overflow-error")
    boxed = escape(make_samples(), 1)
    alias = boxed
    print(boxed is alias)


main()
