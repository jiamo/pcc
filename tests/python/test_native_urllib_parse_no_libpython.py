from __future__ import annotations

import os
import subprocess


def test_urlparse_parse_result_attrs_no_libpython(tmp_path):
    src = tmp_path / "prog.py"
    src.write_text(
        "import urllib.parse\n"
        "def show(text):\n"
        "    u = urllib.parse.urlparse(text)\n"
        "    print(u.scheme, u.netloc, u.path, u.params, u.query, u.fragment)\n"
        "def rewrite(text):\n"
        "    u = urllib.parse.urlparse(text)\n"
        "    print(u._replace(netloc='', scheme='').geturl())\n"
        "def parts(text):\n"
        "    u = urllib.parse.urlparse(text)\n"
        "    print(u.username, u.password, u.hostname, u.port)\n"
        "def main():\n"
        "    show('s://:8081')\n"
        "    show('s://100.118.195.46:8087')\n"
        "    show('s://user@host:9/path,plugin@bind?rule#auth')\n"
        "    rewrite('http://example.com/')\n"
        "    rewrite('http://example.com/a?b=1#frag')\n"
        "    parts('http://example.com/')\n"
        "    parts('http://user:pass@Example.COM:8080/path')\n"
        "    parts('s://:8081')\n"
        "main()\n",
        encoding="utf-8",
    )
    exe = tmp_path / "prog"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
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
    assert run.stdout.splitlines() == [
        "s :8081    ",
        "s 100.118.195.46:8087    ",
        "s user@host:9 /path,plugin@bind  rule auth",
        "/",
        "/a?b=1#frag",
        "None None example.com None",
        "user pass example.com 8080",
        "None None None 8081",
    ]
