from __future__ import annotations

import os
import subprocess


def test_function_doc_and_mutable_metadata_persist(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(
        "def documented():\n"
        '    """source doc"""\n'
        "    pass\n"
        "def exported():\n"
        "    pass\n"
        "print(documented.__doc__)\n"
        "print(documented.__qualname__)\n"
        "exported.__doc__ = documented.__doc__\n"
        'exported.__module__ = "public_api"\n'
        'exported.__qualname__ = "public_api.exported"\n'
        "print(exported.__doc__)\n"
        "print(exported.__module__)\n"
        "print(exported.__qualname__)\n",
        encoding="utf-8",
    )
    executable = tmp_path / "function-metadata"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "cc"
    env["PCC_RUNTIME_HIGH"] = "c"
    compile_result = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr

    run = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=10,
        env=env,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "source doc",
        "documented",
        "source doc",
        "public_api",
        "public_api.exported",
    ]


def test_function_code_signature_metadata(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(
        "import types\n"
        "def implementation(value, *, like=None):\n"
        "    return value\n"
        "def signature(value, extra=None, *args, flag=True, **kwargs):\n"
        "    return value\n"
        "def make_default():\n"
        "    return implementation\n"
        "code = implementation.__code__\n"
        "print(code.co_argcount)\n"
        "print(code.co_kwonlyargcount)\n"
        "print(code.co_varnames[1])\n"
        "print(code is implementation)\n"
        "fallback = getattr(implementation, '__wrapped__', make_default())\n"
        "print(fallback is implementation)\n"
        "signature_code = signature.__code__\n"
        "print(isinstance(signature, types.FunctionType))\n"
        "print(isinstance(signature_code, types.CodeType))\n"
        "print(signature_code.co_argcount)\n"
        "print(signature_code.co_kwonlyargcount)\n"
        "print(signature_code.co_varnames[2])\n"
        "print(signature_code.co_varnames[3])\n"
        "print(signature_code.co_varnames[4])\n"
        "print((signature_code.co_flags & 4) != 0)\n"
        "print((signature_code.co_flags & 8) != 0)\n"
        "print(signature.__defaults__)\n",
        encoding="utf-8",
    )
    executable = tmp_path / "function-code-metadata"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "cc"
    env["PCC_RUNTIME_HIGH"] = "c"
    compile_result = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr

    run = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=10,
        env=env,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "1",
        "1",
        "like",
        "False",
        "True",
        "True",
        "True",
        "2",
        "1",
        "flag",
        "args",
        "kwargs",
        "True",
        "True",
        "(None,)",
    ]
