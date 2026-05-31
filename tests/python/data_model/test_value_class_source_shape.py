from __future__ import annotations

import pytest

from pcc.parse.py_lift import parse_and_lift
from pcc.py_frontend import type_infer
from pcc.py_frontend.types import PyFrontendError


def _infer(source: str):
    mod = parse_and_lift(source, "<valueclass-source-shape>", "value_mod")
    return type_infer.infer_module(mod)


def test_valueclass_accepts_typed_fields_and_typed_initializer():
    typed = _infer(
        "import pcc\n"
        "@pcc.valueclass\n"
        "class Point:\n"
        "    x: int\n"
        "    y: int\n"
        "    def __init__(self, x: int, y: int):\n"
        "        self.x = x\n"
        "        self.y = y\n"
        "p = Point(1, 2)\n"
    )
    assign = typed.body[-1]
    assert assign.value.ty.valueclass is True
    assert tuple(name for name, _ in assign.value.ty.fields) == ("x", "y")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Bad:\n"
            "    x = 1\n",
            "needs an explicit type annotation",
        ),
        (
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Bad:\n"
            "    x: int\n"
            "    def __init__(self, x):\n"
            "        self.x = x\n",
            "needs a typed initializer",
        ),
        (
            "import pcc\n"
            "class Base:\n"
            "    pass\n"
            "@pcc.valueclass\n"
            "class Bad(Base):\n"
            "    x: int\n",
            "cannot subclass",
        ),
        (
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Bad:\n"
            "    __slots__ = ('__weakref__',)\n"
            "    x: int\n",
            "cannot include __weakref__",
        ),
        (
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Bad:\n"
            "    x: int\n"
            "    def __del__(self):\n"
            "        pass\n",
            "cannot define __del__",
        ),
        (
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Bad:\n"
            "    child: 'Bad'\n",
            "recursive valueclass",
        ),
        (
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Left:\n"
            "    right: 'Right'\n"
            "@pcc.valueclass\n"
            "class Right:\n"
            "    left: Left\n",
            "recursive valueclass",
        ),
        (
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Bad:\n"
            "    children: list['Bad']\n",
            "recursive valueclass",
        ),
    ],
)
def test_valueclass_rejects_unsupported_source_shapes(source: str, message: str):
    with pytest.raises(PyFrontendError, match=message):
        _infer(source)


def test_valueclass_rejects_identity_escape_operations():
    with pytest.raises(PyFrontendError, match="identity comparison"):
        _infer(
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Point:\n"
            "    x: int\n"
            "p = Point(1)\n"
            "q = Point(1)\n"
            "same = p is q\n"
        )
    with pytest.raises(PyFrontendError, match=r"id\(\) is not supported"):
        _infer(
            "import pcc\n"
            "@pcc.valueclass\n"
            "class Point:\n"
            "    x: int\n"
            "p = Point(1)\n"
            "ident = id(p)\n"
        )
