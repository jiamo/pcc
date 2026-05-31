from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from pcc.py_frontend.pipeline import compile_python


def _write_and_build_probe(tmp_path: Path, source: str) -> Path:
    src = tmp_path / "value_class_runtime_probe.py"
    exe = tmp_path / "value_class_runtime_probe"
    src.write_text(dedent(source), encoding="utf-8")
    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
    return exe


def _run_probe(exe: Path) -> list[str]:
    proc = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def test_valueclass_runtime_smoke_direct_paths(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
            import pcc

            @pcc.valueclass
            class Point:
                x: int
                y: int

            def area(p: Point) -> int:
                return p.x * p.y

            if __name__ == "__main__":
                p = Point(3, 4)
                q = Point(1, 7)
                print(area(p))
                print(q.x + q.y)
                print(p.x + p.y + q.x + q.y)
        """,
    )
    out = _run_probe(exe)
    assert out == ["12", "8", "15"]


def test_valueclass_runtime_field_read_consistent_with_payload_assignment(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        from typing import Any

        import pcc

        @pcc.valueclass
        class Point:
            x: int
            y: int
            z: bool

        def checksum(p: Point) -> int:
            return p.x + (p.y * 2) + (1 if p.z else 0)

        if __name__ == "__main__":
            p = Point(2, 3, True)
            print(checksum(p))
            print(Point(0, 0, False).x)
            print(p.z)
        """,
    )
    out = _run_probe(exe)
    assert out == ["9", "0", "True"]


def test_valueclass_runtime_valuebox_survives_gc_collect(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        from typing import Any

        import gc
        import pcc

        @pcc.valueclass
        class Point:
            x: int
            y: int

        def to_dyn(v: Any) -> Any:
            return v

        def read_point(v: Any) -> int:
            if isinstance(v, Point):
                return v.x + v.y
            return -1

        if __name__ == "__main__":
            slot = [to_dyn(Point(7, 8))]
            gc.collect()
            print(read_point(slot[0]))
            saved = slot[0]
            slot[0] = None
            gc.collect()
            print(read_point(saved))
        """,
    )
    out = _run_probe(exe)
    assert out == ["15", "15"]


def test_valueclass_runtime_type_error_survives_gc_collect(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        import gc
        import pcc

        @pcc.valueclass
        class Point:
            x: int
            y: int

        def total(p: Point) -> int:
            return p.x + p.y

        def to_dyn(v: object) -> object:
            return v

        if __name__ == "__main__":
            bad = to_dyn(123)
            gc.collect()
            try:
                print(total(bad))
            except Exception as e:
                print(type(e).__name__)
        """,
    )
    out = _run_probe(exe)
    assert out == ["TypeError"]


def test_valueclass_runtime_pointer_payload_survives_dyn_gc_collect(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        from typing import Any

        import gc
        import pcc

        @pcc.valueclass
        class Bag:
            items: list
            count: int

        def to_dyn(v: Any) -> Any:
            return v

        def total(b: Bag) -> int:
            return len(b.items) + b.count

        if __name__ == "__main__":
            bag = Bag([1, 2, 3], 4)
            dyn = to_dyn(bag)
            gc.collect()
            print(total(dyn))
            print(len(dyn.items))
        """,
    )
    out = _run_probe(exe)
    assert out == ["7", "3"]


def test_valueclass_runtime_str_payload_survives_dyn_gc_collect(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        from typing import Any

        import gc
        import pcc

        @pcc.valueclass
        class Label:
            name: str
            score: int

        def to_dyn(v: Any) -> Any:
            return v

        def total(label: Label) -> int:
            return len(label.name) + label.score

        if __name__ == "__main__":
            dyn = to_dyn(Label("alpha", 6))
            gc.collect()
            print(total(dyn))
            print(dyn.name)
        """,
    )
    out = _run_probe(exe)
    assert out == ["11", "alpha"]


def test_valueclass_runtime_pointer_payload_preserves_mutable_identity(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        from typing import Any

        import gc
        import pcc

        @pcc.valueclass
        class Bag:
            items: list
            count: int

        def to_dyn(v: Any) -> Any:
            return v

        def total(bag: Bag) -> int:
            return len(bag.items) + bag.count

        if __name__ == "__main__":
            items = [1, 2]
            dyn = to_dyn(Bag(items, 5))
            gc.collect()
            dyn.items.append(3)
            gc.collect()
            print(len(items))
            print(total(dyn))
        """,
    )
    out = _run_probe(exe)
    assert out == ["3", "8"]


def test_valueclass_runtime_pointer_payload_equality_uses_object_equality(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        import gc
        import pcc

        @pcc.valueclass
        class Bag:
            items: list
            count: int

        def same(left: Bag, right: Bag) -> bool:
            return left == right

        def different(left: Bag, right: Bag) -> bool:
            return left != right

        if __name__ == "__main__":
            first = Bag([1, 2], 3)
            same_contents = Bag([1, 2], 3)
            changed_items = Bag([1, 4], 3)
            changed_count = Bag([1, 2], 4)
            gc.collect()
            print(same(first, same_contents))
            print(same(first, changed_items))
            print(different(first, changed_items))
            print(different(first, changed_count))
        """,
    )
    out = _run_probe(exe)
    assert out == ["True", "False", "True", "True"]


def test_valueclass_runtime_boxed_valueclass_equality_uses_payload_fields(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        from typing import Any

        import gc
        import pcc

        @pcc.valueclass
        class Bag:
            items: list
            count: int

        @pcc.valueclass
        class Sack:
            items: list
            count: int

        def to_dyn(v: Any) -> Any:
            return v

        if __name__ == "__main__":
            left = to_dyn(Bag([1, 2], 3))
            same = to_dyn(Bag([1, 2], 3))
            different_items = to_dyn(Bag([1, 4], 3))
            different_class = to_dyn(Sack([1, 2], 3))
            gc.collect()
            print(left == same)
            print(left == different_items)
            print(left != different_items)
            print(left == different_class)
        """,
    )
    out = _run_probe(exe)
    assert out == ["True", "False", "True", "False"]


def test_valueclass_runtime_boxed_valueclass_hash_matches_payload_equality(tmp_path):
    exe = _write_and_build_probe(
        tmp_path,
        """
        from typing import Any

        import gc
        import pcc

        @pcc.valueclass
        class Label:
            name: str
            score: int

        def to_dyn(v: Any) -> Any:
            return v

        if __name__ == "__main__":
            left = to_dyn(Label("alpha", 6))
            same = to_dyn(Label("alpha", 6))
            changed = to_dyn(Label("alpha", 7))
            table = {left: 42}
            gc.collect()
            print(table[same])
            print(changed in table)
        """,
    )
    out = _run_probe(exe)
    assert out == ["42", "False"]
