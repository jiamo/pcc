"""Phase D1 — descriptor protocol contract.

Locks the contract for descriptor lookup, ``@property`` /
``@classmethod`` / ``@staticmethod`` decorators, and ``__set_name__``
/ ``__slots__`` per ``docs/issues/python-data-model-gaps.md`` Phase
D1.

All tests xfail until D1 lands; flip xfail markers as each sub-protocol
is implemented. Sub-protocols (in implementation order):

1. ``@property`` getter / setter / deleter
2. ``@classmethod`` (cls-as-first-arg dispatch)
3. ``@staticmethod`` (no implicit first arg)
4. Data-descriptor priority (descriptor's ``__get__`` wins over
   ``instance.__dict__``)
5. Non-data descriptor: instance dict overrides descriptor
6. ``__set_name__`` (class-body callback)
7. ``__slots__`` storage layout (no ``__dict__``)
"""
from __future__ import annotations

import subprocess
import textwrap

import pytest


def _compile_and_run(tmp_path, source: str) -> subprocess.CompletedProcess[str]:
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    compile_python(str(src), str(exe), ir_scaffold_mode="on")
    return subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )


# ---------------------------------------------------------------------------
# @property getter / setter / deleter
# ---------------------------------------------------------------------------


def test_property_getter_basic(tmp_path):
    result = _compile_and_run(tmp_path, """
        class Box:
            def __init__(self, v):
                self._v = v
            @property
            def value(self):
                return self._v

        def main() -> None:
            b = Box(42)
            print(b.value)  # Should call getter, not return descriptor
            print(type(b.value).__name__)  # Should be 'int'

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split("\n") == ["42", "int"]


def test_property_fget_can_alias_an_instance_method(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "property_fget_alias.py"
    exe = tmp_path / "property_fget_alias.out"
    src.write_text(
        textwrap.dedent(
            """
            class Box:
                @property
                def value(self):
                    return 42

                get_value = value.fget

            print(Box().get_value())
            """
        ),
        encoding="utf-8",
    )
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
        backend="self",
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "42\n"


def test_cached_property_getter_basic(tmp_path):
    result = _compile_and_run(tmp_path, """
        class Box:
            def __init__(self, v):
                self._v = v

            @cached_property
            def value(self):
                return self._v

        def main() -> None:
            box = Box(42)
            print(box.value)

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "42"


def test_property_setter(tmp_path):  # passes today — locked by D1 baseline
    result = _compile_and_run(tmp_path, """
        class Box:
            def __init__(self):
                self._v = 0
            @property
            def value(self):
                return self._v
            @value.setter
            def value(self, x):
                self._v = x * 2  # double on assignment

        def main() -> None:
            b = Box()
            b.value = 21
            print(b.value)

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "42"


def test_property_readonly_raises(tmp_path):
    result = _compile_and_run(tmp_path, """
        class Box:
            @property
            def value(self):
                return 7

        def main() -> None:
            b = Box()
            try:
                b.value = 10  # no setter — must raise AttributeError
                print("no_error")
            except AttributeError:
                print("AttributeError")

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AttributeError"


# ---------------------------------------------------------------------------
# @classmethod / @staticmethod
# ---------------------------------------------------------------------------


def test_classmethod_binds_class(tmp_path):  # passes today — locked by D1 baseline
    result = _compile_and_run(tmp_path, """
        class Counter:
            count = 0
            @classmethod
            def incr(cls):
                cls.count = cls.count + 1
                return cls.count

        def main() -> None:
            print(Counter.incr())  # 1
            print(Counter.incr())  # 2
            c = Counter()
            print(c.incr())        # 3 (method called via instance still binds class)

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split("\n") == ["1", "2", "3"]


def test_staticmethod_no_self(tmp_path):  # passes today — locked by D1 baseline
    result = _compile_and_run(tmp_path, """
        class M:
            @staticmethod
            def add(a, b):
                return a + b

        def main() -> None:
            print(M.add(3, 4))    # via class
            m = M()
            print(m.add(5, 6))    # via instance — must NOT pass m as self

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split("\n") == ["7", "11"]


# ---------------------------------------------------------------------------
# Descriptor priority — data descriptor beats instance __dict__
# ---------------------------------------------------------------------------


def test_data_descriptor_overrides_instance_dict(tmp_path):
    """If class has a data descriptor (with __set__) for attr X, accessing
    instance.X must call descriptor.__get__ even when X is also in
    instance.__dict__. This is core to property semantics."""
    result = _compile_and_run(tmp_path, """
        class Desc:
            def __get__(self, obj, owner):
                return "desc-get"
            def __set__(self, obj, value):
                pass

        class Box:
            x = Desc()

        def main() -> None:
            b = Box()
            # Force-stuff x into instance dict via dict access:
            b.__dict__["x"] = "instance"
            # Lookup must still go to Desc.__get__ because Desc has __set__
            print(b.x)

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "desc-get"


def test_non_data_descriptor_loses_to_instance_dict(tmp_path):
    """If descriptor has __get__ but NOT __set__, instance.__dict__ wins.
    This is how regular methods work — they're non-data descriptors."""
    result = _compile_and_run(tmp_path, """
        class Desc:
            def __get__(self, obj, owner):
                return "desc-get"
            # No __set__

        class Box:
            x = Desc()

        def main() -> None:
            b = Box()
            b.__dict__["x"] = "instance"
            # Non-data: instance dict wins
            print(b.x)

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "instance"


# ---------------------------------------------------------------------------
# __set_name__ — class-body callback
# ---------------------------------------------------------------------------


def test_set_name_called_at_class_creation(tmp_path):
    result = _compile_and_run(tmp_path, """
        class Tag:
            def __set_name__(self, owner, name):
                self.attached_to = name

        class Holder:
            x = Tag()
            y = Tag()

        def main() -> None:
            print(Holder.x.attached_to)
            print(Holder.y.attached_to)

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split("\n") == ["x", "y"]


# ---------------------------------------------------------------------------
# __slots__ — fixed-attribute storage
# ---------------------------------------------------------------------------


def test_slots_basic_storage(tmp_path):
    result = _compile_and_run(tmp_path, """
        class Point:
            __slots__ = ("x", "y")

        def main() -> None:
            p = Point()
            p.x = 3
            p.y = 4
            print(p.x + p.y)

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "7"


def test_slots_rejects_unknown_attribute(tmp_path):
    result = _compile_and_run(tmp_path, """
        class Point:
            __slots__ = ("x", "y")

        def main() -> None:
            p = Point()
            try:
                p.z = 5  # 'z' not in __slots__ — must raise AttributeError
                print("no_error")
            except AttributeError:
                print("AttributeError")

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AttributeError"


def test_slots_no_dict(tmp_path):
    result = _compile_and_run(tmp_path, """
        class Point:
            __slots__ = ("x",)

        def main() -> None:
            p = Point()
            try:
                _ = p.__dict__   # __slots__ classes have no __dict__
                print("has_dict")
            except AttributeError:
                print("no_dict")

        if __name__ == "__main__":
            main()
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "no_dict"
