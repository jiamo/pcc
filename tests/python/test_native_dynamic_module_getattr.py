"""Dynamic native-module getattr preserves missing-attribute exceptions."""
from __future__ import annotations

import os
import subprocess


def test_dynamic_module_getattr_inside_generator_is_catchable(tmp_path):
    support = tmp_path / "support_mod.py"
    support.write_text('value = "ok"\n', encoding="utf-8")
    main = tmp_path / "main.py"
    main.write_text(
        "import support_mod\n"
        "def collect(names):\n"
        "    def values():\n"
        "        for name in names:\n"
        "            try:\n"
        "                value = getattr(support_mod, name)\n"
        "            except AttributeError:\n"
        "                pass\n"
        "            else:\n"
        "                yield (value, name)\n"
        "    return list(values())\n"
        "print(collect(['value', 'missing']))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "cc"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20, env=env
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "[('ok', 'value')]\n"
