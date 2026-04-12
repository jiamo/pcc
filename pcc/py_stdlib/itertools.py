"""pcc.py_stdlib.itertools — narrow ``itertools`` replacement.

Scope matches pcc's own usage: chain, repeat, islice, product,
groupby, count.
"""
from __future__ import annotations


def chain(*iterables):
    for it in iterables:
        for item in it:
            yield item


def repeat(value, times=None):
    if times is None:
        while True:
            yield value
    else:
        for _ in range(times):
            yield value


def islice(iterable, *args):
    """``islice(iterable, stop)`` or ``islice(iterable, start, stop[, step])``."""
    if len(args) == 1:
        start, stop, step = 0, args[0], 1
    elif len(args) == 2:
        start, stop, step = args[0], args[1], 1
    elif len(args) == 3:
        start, stop, step = args
    else:
        raise TypeError("islice expects 1..3 positional args after iterable")
    if stop is None:
        # islice(iterable, start, None) is "from start to end, step".
        stop = 1 << 62
    i = 0
    nxt = start
    for item in iterable:
        if i >= stop:
            break
        if i == nxt:
            yield item
            nxt += step
        i += 1


def count(start=0, step=1):
    i = start
    while True:
        yield i
        i += step


def product(*iterables, repeat: int = 1):
    """Cartesian product."""
    pools = [list(it) for it in iterables] * repeat
    result: list[tuple] = [()]
    for pool in pools:
        new_result: list[tuple] = []
        for prefix in result:
            for item in pool:
                new_result.append(prefix + (item,))
        result = new_result
    for r in result:
        yield r


def groupby(iterable, key=None):
    if key is None:
        key = lambda x: x
    last_key = object()
    group: list = []
    for item in iterable:
        k = key(item)
        if group and k != last_key:
            yield last_key, iter(group)
            group = []
        group.append(item)
        last_key = k
    if group:
        yield last_key, iter(group)
