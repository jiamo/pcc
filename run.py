#!/usr/bin/env python3
import argparse
import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class CleanTarget:
    make_dir: str | None = None
    make_target: str | None = None
    make_if_exists: tuple[str, ...] = ()
    restore_paths: tuple[str, ...] = ()
    remove_paths: tuple[str, ...] = ()
    remove_globs: tuple[str, ...] = ()
    remove_dirs: tuple[str, ...] = ()


CLEAN_TARGETS = {
    "generated": CleanTarget(
        remove_paths=(
            "a.out",
            "lextab.py",
            "yacctab.py",
            "parser.out",
            "temp.bcode",
            "temp.ir",
            "temp.ooptimize.bcode",
        ),
        remove_dirs=("__pycache__",),
    ),
    "postgres": CleanTarget(
        make_dir="projects/postgresql-17.4",
        make_target="distclean",
        make_if_exists=("GNUmakefile", "config.status"),
    ),
    "readline": CleanTarget(
        make_dir="projects/readline-8.2",
        make_target="distclean",
        make_if_exists=("Makefile",),
    ),
    "zlib": CleanTarget(
        make_dir="projects/zlib-1.3.1",
        make_target="distclean",
        make_if_exists=("Makefile",),
        restore_paths=(
            "projects/zlib-1.3.1/Makefile",
            "projects/zlib-1.3.1/zconf.h",
        ),
        remove_paths=("projects/zlib-1.3.1/zlib.pc",),
    ),
    "nginx": CleanTarget(
        remove_paths=("projects/nginx-1.28.3/Makefile",),
        remove_dirs=("projects/nginx-1.28.3/objs",),
    ),
}


def _repo_root():
    return os.path.dirname(os.path.abspath(__file__))


def _run(cmd, cwd):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "unknown error").strip()
    if len(detail) > 400:
        detail = detail[:400]
    raise RuntimeError(f"{' '.join(cmd)} failed in {cwd}: {detail}")


def _tracked_restore(repo_root, paths):
    if not paths:
        return
    _run(
        ["git", "restore", "--staged", "--worktree", "--source=HEAD", "--", *paths],
        cwd=repo_root,
    )


def _remove_path(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        os.unlink(path)


def clean(target_names=None, repo_root=None):
    repo_root = os.path.abspath(repo_root or _repo_root())
    if not target_names:
        target_names = tuple(CLEAN_TARGETS.keys())

    for name in target_names:
        if name not in CLEAN_TARGETS:
            known = ", ".join(sorted(CLEAN_TARGETS))
            raise ValueError(f"unknown clean target '{name}' (expected one of: {known})")

        spec = CLEAN_TARGETS[name]

        if spec.make_dir and spec.make_target:
            make_dir = os.path.join(repo_root, spec.make_dir)
            should_run = True
            if spec.make_if_exists:
                should_run = any(
                    os.path.exists(os.path.join(make_dir, marker))
                    for marker in spec.make_if_exists
                )
            if should_run:
                _run(["make", "-C", make_dir, spec.make_target], cwd=repo_root)

        if spec.restore_paths:
            _tracked_restore(repo_root, list(spec.restore_paths))

        for relpath in spec.remove_paths:
            _remove_path(os.path.join(repo_root, relpath))

        for pattern in spec.remove_globs:
            for path in glob.glob(os.path.join(repo_root, pattern)):
                _remove_path(path)

        for relpath in spec.remove_dirs:
            _remove_path(os.path.join(repo_root, relpath))


def _build_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    clean_parser = subparsers.add_parser("clean", help="Remove generated build artifacts")
    clean_parser.add_argument(
        "targets",
        nargs="*",
        help=f"Optional clean targets. Defaults to all: {', '.join(CLEAN_TARGETS)}",
    )

    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)

    if args.command == "clean":
        clean(args.targets)
        return 0

    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
