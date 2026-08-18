"""Heavy-object provenance workload for the granule span-lookup row.

This is the workload the ARCH-P1-GRANULE-SPAN-LOOKUP-RADIX baseline_metric is
recorded against.  It exists as a file so the recorded share (span lookup
12.2% of self samples) can be re-measured instead of re-derived: every
allocation, free, attribute store and method dispatch below drives the
allocator's object-family provenance path.

Compile with pcc in DEFAULT mode so the pcc-Python runtime ports are linked
(PCC_RUNTIME_CC=cc would link the C sources and measure the wrong tree).
"""


class Node:
    def __init__(self, value: int) -> None:
        self.value = value
        self.next = None

    def bump(self) -> int:
        return self.value + 1


class Holder:
    def __init__(self, tag: int) -> None:
        self.tag = tag

    def key(self) -> int:
        return self.tag * 3


def build_chain(n: int) -> Node:
    head = Node(0)
    i = 1
    while i < n:
        node = Node(i)
        node.next = head
        head = node
        i = i + 1
    return head


def walk(head: Node) -> int:
    total = 0
    cur = head
    while cur is not None:
        total = total + cur.bump()
        cur = cur.next
    return total


def main() -> None:
    total = 0
    rounds = 400
    nodes = 4000
    r = 0
    while r < rounds:
        head = build_chain(nodes)
        w = 0
        while w < 6:
            total = total + walk(head)
            w = w + 1
        table = {}
        k = 0
        while k < nodes:
            holder = Holder(k)
            table[k] = holder
            k = k + 1
        total = total + len(table)
        r = r + 1
    print(total)


main()
