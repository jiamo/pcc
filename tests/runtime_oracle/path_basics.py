"""Runtime oracle for the os.path / sys.platform / print(file=) native
dispatch wave (2026-04-28-recapture-A).

Exercises every native dispatch + runtime helper added in that wave so
the oracle harness compares the actual byte-level stdout/stderr/exit
across cc-built and pcc-built archive variants. If a helper diverges
between archives, this corpus surfaces it.

Output is deterministic — uses only paths under the repo root that
must always exist (the entry point itself, /tmp).
"""
import os
import sys


def main() -> int:
    print("--- os.path string ops ---")
    print("join:", os.path.join("a", "b", "c"))
    print("join-abs:", os.path.join("/tmp", "x"))
    print("dirname:", os.path.dirname("/foo/bar/baz"))
    print("dirname-rel:", os.path.dirname("foo/bar"))
    print("dirname-leaf:", os.path.dirname("file"))
    print("dirname-root:", os.path.dirname("/"))
    print("basename:", os.path.basename("/foo/bar/baz"))
    print("basename-trailing:", os.path.basename("/foo/bar/"))
    print("basename-leaf:", os.path.basename("file"))

    print("--- os.path filesystem ---")
    # /tmp must always exist on POSIX hosts the oracle runs on.
    print("isdir /tmp:", os.path.isdir("/tmp"))
    print("isfile /tmp:", os.path.isfile("/tmp"))
    print("exists /tmp:", os.path.exists("/tmp"))
    nonexist = "/tmp/__pcc_oracle_nonexistent__"
    print("isdir missing:", os.path.isdir(nonexist))
    print("isfile missing:", os.path.isfile(nonexist))
    print("exists missing:", os.path.exists(nonexist))

    print("--- os.path.abspath ---")
    print("abs already-abs:", os.path.abspath("/tmp"))

    print("--- os.getcwd / os.access ---")
    cwd = os.getcwd()
    print("cwd-abs:", cwd[:1] == "/")
    print("access /tmp F_OK:", os.access("/tmp", os.F_OK))
    print("access /tmp R_OK:", os.access("/tmp", os.R_OK))
    print("access /tmp X_OK:", os.access("/tmp", os.X_OK))
    print("access missing:", os.access(nonexist, os.F_OK))
    print("X_OK is 1:", os.X_OK == 1)
    print("F_OK is 0:", os.F_OK == 0)

    print("--- os.environ.get ---")
    # PCC_PATH_ORACLE_TEST is set by the harness invocation; missing
    # default returns the supplied value rather than raising.
    print("env-set:", os.environ.get("PCC_PATH_ORACLE_TEST", "default"))
    print("env-missing:", os.environ.get("__PCC_NO_SUCH_VAR__", "fallback"))

    print("--- sys.platform ---")
    plat = sys.platform
    # Don't echo the literal value (varies by host); instead assert the
    # value is one of the known C-side constants.
    if plat == "darwin":
        print("plat: darwin")
    elif plat == "linux":
        print("plat: linux")
    elif plat == "win32":
        print("plat: win32")
    elif plat == "freebsd":
        print("plat: freebsd")
    else:
        print("plat: other")

    print("--- print(file=sys.stderr) ---")
    print("stdout: 1")
    print("stderr-msg", file=sys.stderr)
    print("stdout: 2")

    return 0


sys.exit(main())
