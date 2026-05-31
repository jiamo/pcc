from __future__ import annotations

from pcc.py_frontend import parser
from pcc.py_frontend.py_ast import Import


def test_os_io_re_math_import_shapes():
    mod = parser.parse(
        "import os\n"
        "import io\n"
        "import re\n"
        "import math\n",
        "stdlib_core_probe.py",
    )
    for stmt in mod.body:
        assert isinstance(stmt, Import)
