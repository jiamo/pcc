from __future__ import annotations

import os
import subprocess


def _run(tmp_path, source):
    src = tmp_path / "p.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "p_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_list_map_builtin_str_no_libpython(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    print(list(map(str, [1, 2, 3])))\n"
        "    print(list(map(str, ['a', 'bb'])))\n"
        "main()\n",
    )
    assert out.splitlines() == ["['1', '2', '3']", "['a', 'bb']"]


def test_tuple_map_builtin_chr_at_module_scope_no_libpython(tmp_path):
    out = _run(
        tmp_path,
        "chars = tuple(map(chr, range(4)))\n" "print(chars)\n",
    )
    assert out.strip() == "('\\x00', '\\x01', '\\x02', '\\x03')"


def test_list_map_filter_user_functions_still_work(tmp_path):
    out = _run(
        tmp_path,
        "def sq(x):\n"
        "    return x * x\n"
        "def keep(x):\n"
        "    return x > 2\n"
        "def main():\n"
        "    print(list(map(sq, [1, 2, 3])))\n"
        "    print(list(filter(keep, [1, 2, 3, 4])))\n"
        "main()\n",
    )
    assert out.splitlines() == ["[1, 4, 9]", "[3, 4]"]


def test_any_all_map_lambda_no_libpython(tmp_path):
    out = _run(
        tmp_path,
        "class Box:\n"
        "    def __init__(self, flag):\n"
        "        self.flag = flag\n"
        "def main():\n"
        "    nums = [0, 1, 2]\n"
        "    boxes = [Box(False), Box(True)]\n"
        "    print(any(map(lambda x: x > 1, nums)))\n"
        "    print(all(map(lambda x: x < 3, nums)))\n"
        "    print(any(map(lambda o: o.flag, boxes)))\n"
        "main()\n",
    )
    assert out.splitlines() == ["True", "True", "True"]


def test_filter_lambda_method_next_default_no_libpython(tmp_path):
    out = _run(
        tmp_path,
        "class Route:\n"
        "    def __init__(self):\n"
        "        self.alive = True\n"
        "    def match_rule(self, host, port):\n"
        "        return host == 'example.com' and port == 80\n"
        "def schedule(rserver, host_name, port):\n"
        "    filter_cond = lambda o: o.alive and o.match_rule(host_name, port)\n"
        "    return next(filter(filter_cond, rserver), None)\n"
        "def main():\n"
        "    r = Route()\n"
        "    chosen = schedule([r], 'example.com', 80)\n"
        "    print(chosen is r)\n"
        "main()\n",
    )
    assert out.splitlines() == ["True"]
