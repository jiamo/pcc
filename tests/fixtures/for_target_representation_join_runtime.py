from typing import Any


def last(values: list[Any]) -> Any:
    item: int = 7
    for item in values:
        if item == "skip":
            continue
    return item


def enumerate_probe(lines: list[str]) -> int:
    indexes = []
    index: int = 99
    for index, line in enumerate(lines):
        indexes.append(index)
    total: int = 0
    for index in indexes:
        total = total + index
    for index in reversed(indexes):
        total = total + index
    return total + index


def unbound_enumerate_probe(lines: list[str]) -> int:
    indexes = []
    for index, line in enumerate(lines):
        indexes.append(index)
    for index in indexes:
        pass
    for index in reversed(indexes):
        pass
    return len(indexes)


def range_probe(n: int) -> int:
    value: int = 7
    for value in range(n):
        pass
    return value


def tuple_empty_probe() -> Any:
    values = ()
    value: int = 7
    for value in values:
        pass
    return value


def tuple_nonempty_probe() -> Any:
    values = ("a", "b")
    value: int = 7
    for value in values:
        pass
    return value


def range_body_rebind_probe() -> int:
    total: int = 0
    for value in range(3):
        total = total + value
        value = 100
    return total


def nested_same_name_range_probe() -> int:
    total: int = 0
    for value in range(3):
        for value in range(2):
            total = total + 1
    return total


print(last([]))
print(last(["a", "b"]))
print(enumerate_probe([]))
print(enumerate_probe(["a", "b", "c"]))
print(unbound_enumerate_probe([]))
print(unbound_enumerate_probe(["a", "b", "c"]))
print(range_probe(0))
print(range_probe(2))
print(tuple_empty_probe())
print(tuple_nonempty_probe())
print(range_body_rebind_probe())
print(nested_same_name_range_probe())
