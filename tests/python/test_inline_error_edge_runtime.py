"""Direct inline error edges must not change what a program does.

The direct/no-text self backend replaces every post-call ``call.cont`` /
``call.err.cleanup`` / per-line ``err.frame`` triplet with one inline edge, an
edge-reached cleanup block and one shared traceback landing per (function,
error target) whose (line, source) pair comes from module tables.  The text
oracle keeps the historical CFG.  Both must produce byte-identical stdout,
stderr (including the multi-frame traceback with source lines) and exit code
on the refcount (0), generational (3) and relocating (4) collectors.
"""

from __future__ import annotations

import os
import subprocess

PROGRAM = """\
import traceback


class T:
    def __init__(self, tag):
        self.tag = tag

    def __del__(self):
        print('freed', self.tag)


def inner(text: str) -> int:
    return int(text)


def middle(text: str) -> int:
    return inner(text) + 1


def outer(text: str) -> int:
    value = middle(text)
    return value * 2


def two_sites(flag: bool) -> int:
    if flag:
        return int('first-site')
    return int('second-site')


def raiser(a, b) -> int:
    raise RuntimeError('boom ' + a.tag + ' ' + str(len(b)))


def lookup(table, key):
    return table[key]


def loop_probe(values) -> int:
    total = 0
    for item in values:
        try:
            total += outer(item)
        except ValueError as exc:
            print('loop caught', type(exc).__name__, str(exc))
    return total


def reraise(text: str) -> int:
    try:
        return inner(text)
    except ValueError:
        print('reraise')
        raise


print('start')
print('ok', outer('20'))
for flag in (True, False):
    try:
        two_sites(flag)
    except ValueError:
        print(traceback.format_exc())
try:
    raiser(T('t1'), [T('t2')])
except RuntimeError as exc:
    print('caught', str(exc))
try:
    lookup({'a': 1}, 'missing')
except KeyError as exc:
    print('caught KeyError', str(exc))
print('loop', loop_probe(['1', 'x', '2', 'y', '3']))
try:
    reraise('q')
except ValueError as exc:
    print('outer caught', str(exc))
print('before uncaught')
outer('zzz')
print('unreachable')
"""

_FLAGS = {
    "PCC_DIRECT_INDEXED_KERNEL_CAPTURE": "1",
    "PCC_DIRECT_INDEXED_KERNEL_EMIT": "1",
    "PCC_DIRECT_INDEXED_KERNEL_FUSE_USES": "1",
    "PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE": "1",
}


def _build(tmp_path, *, direct: bool):
    src = tmp_path / "prog.py"
    if not src.exists():
        src.write_text(PROGRAM, encoding="utf-8")
    exe = tmp_path / ("prog_direct" if direct else "prog_text")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    for name in _FLAGS:
        env.pop(name, None)
    if direct:
        env.update(_FLAGS)
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=900, env=env,
    )
    assert build.returncode == 0, build.stderr
    return exe


def _run(exe, backend: str):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    return subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )


def test_inline_error_edges_match_text_oracle_on_gc0_3_4(tmp_path):
    text_exe = _build(tmp_path, direct=False)
    direct_exe = _build(tmp_path, direct=True)
    for backend in ("0", "3", "4"):
        text = _run(text_exe, backend)
        direct = _run(direct_exe, backend)
        assert text.returncode == 1, (backend, text.stderr)
        assert direct.returncode == text.returncode, (backend, direct.stderr)
        assert direct.stdout == text.stdout, backend
        assert direct.stderr == text.stderr, backend
    # The uncaught traceback walks four frames; every frame carries its own
    # line and source text, so the shared landing's table lookup is exercised
    # on distinct payloads.  The two_sites function raises from two different
    # lines through one landing.
    stderr = direct.stderr
    for fragment in (
        'line 21, in outer',
        'value = middle(text)',
        'line 17, in middle',
        'return inner(text) + 1',
        'line 13, in inner',
        'return int(text)',
        "ValueError: invalid literal for int() with base 10: 'zzz'",
    ):
        assert fragment in stderr, (fragment, stderr)
    assert "first-site" in direct.stdout and "second-site" in direct.stdout
    assert "line 27, in two_sites" in direct.stdout
    assert "line 28, in two_sites" in direct.stdout
    assert "caught KeyError 'missing'" in direct.stdout
