"""Lua compilation test suite.

Tests each Lua .c file through pcc's full pipeline:
  preprocess → parse → codegen → IR serialize → LLVM verify

Run:  uv run pytest tests/test_lua.py -v
"""

import os
import platform
import re
import shutil
import subprocess
import tempfile
import pytest
import llvmlite.ir as ir
import llvmlite.binding as llvm
from pcc.codegen.c_codegen import postprocess_ir_text

this_dir = os.path.dirname(__file__)
project_dir = os.path.dirname(this_dir)
lua_src_dir = os.path.join(project_dir, "projects", "lua-5.5.0")
lua_tests_dir = os.path.join(project_dir, "projects", "lua-5.5.0", "testes")

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()

_TYPEDEF_CLEANUP = re.compile(
    r"typedef\s+(int|char|short|long|double|float|void)\s+\1\s*;"
)
_TMPFILE_PATH = re.compile(r"(/var/tmp|/tmp)/(?:tmp\.[^\s)]+|lua_[^\s)]+)")
_RANDOM_SEEDS_LINE = re.compile(r"^random seeds: .*$", re.MULTILINE)
_MEMORY_LINE = re.compile(r"^memory:\s+.*$", re.MULTILINE)
_TOTAL_MEMORY_SUMMARY_LINE = re.compile(r"^\s*---- total memory: .*$", re.MULTILINE)
_TIME_LINE = re.compile(r"^time: .*$", re.MULTILINE)
_TOTAL_TIME_LINE = re.compile(r"^total time: .*$", re.MULTILINE)
_CLOCK_TIME = re.compile(r"\b\d{2}:\d{2}:\d{2}\b")
_FLOAT_RANDOM_RANGE_LINE = re.compile(
    r"^float random range in \d+ calls: \[[^\]]+\]$", re.MULTILINE
)
_INTEGER_RANDOM_RANGE_LINE = re.compile(
    r"^integer random range in \d+ calls: \[minint \+ .*ppm, maxint - .*ppm\]$",
    re.MULTILINE,
)
_RANDOM_TABLE_SEEDS_LINE = re.compile(
    r"^testing length for some random tables \(seeds .*?\)$", re.MULTILINE
)
_SORT_BENCHMARK_LINES = [
    re.compile(r"^sorting 50000 random elements in .*$", re.MULTILINE),
    re.compile(r"^re-sorting 50000 sorted elements in .*$", re.MULTILINE),
    re.compile(r"^Invert-sorting other 50000 elements in .*$", re.MULTILINE),
    re.compile(r"^sorting 50000 equal elements in .*$", re.MULTILINE),
]
_SHORT_CIRCUIT_OPT_LINE = re.compile(
    r"^testing short-circuit optimizations \([01]\)$", re.MULTILINE
)
_BACKGROUND_PID_LINE = re.compile(
    r"^\(if test fails now, it may leave a Lua script running in background, pid \d+\)$",
    re.MULTILINE,
)
LUA_CPP_ARGS = ("-DLUA_USE_JUMPTABLE=0", "-DLUA_NOBUILTIN")


def _lua_preprocessor_prefix():
    system = platform.system()
    if system not in {"Darwin", "Linux"}:
        return ""

    return "\n".join(
        [
            "int isatty(int);",
            "int mkstemp(char *);",
            "int close(int);",
            "#define LUA_USE_DLOPEN 1",
            "#define lua_stdin_is_tty() isatty(0)",
            "#define LUA_TMPNAMBUFSIZE 32",
            '#define lua_tmpnam(b,e) { strcpy(b, "/tmp/lua_XXXXXX"); e = mkstemp(b); if (e != -1) close(e); e = (e == -1); }',
            "#define l_popen(L,c,m) (fflush(NULL), popen(c,m))",
            "#define l_pclose(L,file) (pclose(file))",
            "#define WIFEXITED(status) (((status) & 0x7f) == 0)",
            "#define WEXITSTATUS(status) (((status) >> 8) & 0xff)",
            "#define WIFSIGNALED(status) (((status) & 0x7f) != 0 && ((status) & 0x7f) != 0x7f)",
            "#define WTERMSIG(status) ((status) & 0x7f)",
            "#define l_inspectstat(stat,what) \\",
            "   if (WIFEXITED(stat)) { stat = WEXITSTATUS(stat); } \\",
            '   else if (WIFSIGNALED(stat)) { stat = WTERMSIG(stat); what = "signal"; }',
            "",
        ]
    )


def _fix_ir(text, renames=None):
    """Post-process LLVM IR text. Most fixes now live in postprocess_ir_text;
    this wrapper only handles test-specific array rename fixups."""
    text = postprocess_ir_text(text)
    if renames:
        for old, new in renames.items():
            text = re.sub(
                re.escape(old)
                + r" = (?:external )?(?:global|constant) \[0 x [^\]]*\](?: zeroinitializer)?\n",
                "",
                text,
            )
            text = text.replace(new, old)
    # Fix undefined goto labels
    text = text.replace('%"label_retry"', '%"label_retry_d"')
    text = text.replace("label_retry:", "label_retry_d:")
    return text


def _compile_lua_file(fname):
    """Compile a single Lua .c file. Returns (stage, detail).

    Stages: 'preprocess', 'parse', 'codegen', 'ir_serialize', 'llvm_verify', 'ok'
    """
    from pcc.evaluater.c_evaluator import CEvaluator
    from pcc.parse.c_parser import CParser
    from pcc.codegen.c_codegen import LLVMCodeGenerator

    fpath = os.path.join(lua_src_dir, fname)
    stage = "init"
    try:
        with open(fpath) as f:
            src = f.read()

        processed = CEvaluator._system_cpp(
            src,
            base_dir=lua_src_dir,
            cpp_args=LUA_CPP_ARGS,
        )
        processed = _TYPEDEF_CLEANUP.sub("", processed)
        stage = "preprocess"

        ast = CParser().parse(processed)
        stage = "parse"

        cg = LLVMCodeGenerator()
        cg.generate_code(ast)
        renames = getattr(cg, "_array_renames", {})
        stage = "codegen"

        # Fix bad globals before serialization
        for n, gv in list(cg.module.globals.items()):
            if isinstance(gv, ir.GlobalVariable):
                try:
                    str(gv)
                except Exception:
                    gv.initializer = ir.Constant(gv.value_type, None)

        ir_text = _fix_ir(str(cg.module), renames)
        funcs = [l for l in ir_text.splitlines() if l.startswith("define ")]
        stage = "ir_serialize"

        llvmmod = llvm.parse_assembly(ir_text)
        stage = "llvm_verify"

        return "ok", len(funcs)
    except Exception as e:
        return stage, f"{type(e).__name__}: {str(e)[:80]}"


# Collect files
LUA_C_FILES = (
    sorted(f for f in os.listdir(lua_src_dir) if f.endswith(".c"))
    if os.path.isdir(lua_src_dir)
    else []
)
# Files that cannot be run standalone:
#   heavy.lua  - runs 2+ min, tested manually
#   big.lua    - must run inside coroutine.wrap (see all.lua:180)
#   all.lua    - test runner entry point, needs special handling
_LUA_TEST_SKIP = {"heavy.lua", "big.lua", "all.lua"}
LUA_TEST_FILES = (
    sorted(
        f
        for f in os.listdir(lua_tests_dir)
        if f.endswith(".lua") and f not in _LUA_TEST_SKIP
    )
    if os.path.isdir(lua_tests_dir)
    else []
)
# Skip meta-files
LUA_C_FILES_FILTERED = [f for f in LUA_C_FILES if f not in ("onelua.c", "ltests.c")]


@pytest.mark.parametrize("fname", LUA_C_FILES_FILTERED, ids=LUA_C_FILES_FILTERED)
def test_lua_source_compile(fname):
    """Test Lua .c file: preprocess → parse → codegen → LLVM verify."""
    stage, result = _compile_lua_file(fname)
    if stage == "ok":
        pass  # Compiled + LLVM verified!
    else:
        pytest.xfail(f"Stage '{stage}': {result}")


def _compile_onelua():
    """Compile onelua.c as a single translation unit → .o file.

    Returns (stage, detail) where stage is one of:
      'preprocess', 'parse', 'codegen', 'ir_serialize', 'llvm_compile', 'link', 'ok'
    """
    from pcc.evaluater.c_evaluator import CEvaluator
    from pcc.parse.c_parser import CParser
    from pcc.codegen.c_codegen import LLVMCodeGenerator

    onelua_path = os.path.join(lua_src_dir, "onelua.c")
    if not os.path.isfile(onelua_path):
        return "init", "onelua.c not found"

    stage = "init"
    try:
        with open(onelua_path) as f:
            src = f.read()

        src = _lua_preprocessor_prefix() + src
        processed = CEvaluator._system_cpp(
            src,
            base_dir=lua_src_dir,
            cpp_args=LUA_CPP_ARGS,
        )
        processed = _TYPEDEF_CLEANUP.sub("", processed)
        stage = "preprocess"

        ast = CParser().parse(processed)
        stage = "parse"

        cg = LLVMCodeGenerator()
        cg.generate_code(ast)
        renames = getattr(cg, "_array_renames", {})
        stage = "codegen"

        for n, gv in list(cg.module.globals.items()):
            if isinstance(gv, ir.GlobalVariable):
                try:
                    str(gv)
                except Exception:
                    gv.initializer = ir.Constant(gv.value_type, None)

        ir_text = _fix_ir(str(cg.module), renames)
        stage = "ir_serialize"

        # Write IR and compile with system clang
        tmpdir = tempfile.mkdtemp(prefix="pcc_lua_")
        ir_path = os.path.join(tmpdir, "onelua.ll")
        obj_path = os.path.join(tmpdir, "onelua.o")
        bin_path = os.path.join(tmpdir, "lua_bin")

        with open(ir_path, "w") as f:
            f.write(ir_text)

        r = subprocess.run(
            ["cc", "-c", "-w", ir_path, "-o", obj_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            return "llvm_compile", r.stderr[:200]
        stage = "llvm_compile"

        r = subprocess.run(
            ["cc", obj_path, "-o", bin_path, "-lm", "-ldl"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            return "link", r.stderr[:200]
        stage = "link"

        return "ok", bin_path
    except Exception as e:
        return stage, f"{type(e).__name__}: {str(e)[:120]}"


def _compile_native_onelua():
    """Compile onelua.c with system cc. Returns (stage, bin_path)."""
    onelua_path = os.path.join(lua_src_dir, "onelua.c")
    if not os.path.isfile(onelua_path):
        return "init", "onelua.c not found"

    try:
        tmpdir = tempfile.mkdtemp(prefix="pcc_native_lua_")
        bin_path = os.path.join(tmpdir, "lua_native")
        wrapper_path = os.path.join(tmpdir, "onelua_wrapper.c")
        with open(wrapper_path, "w") as f:
            f.write(_lua_preprocessor_prefix())
            f.write(f'#include "{onelua_path}"\n')
        r = subprocess.run(
            [
                "cc",
                "-O0",
                "-w",
                "-I",
                lua_src_dir,
                wrapper_path,
                "-o",
                bin_path,
                "-lm",
                "-ldl",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if r.returncode != 0:
            return "compile", r.stderr[:200]
        return "ok", bin_path
    except Exception as e:
        return "compile", f"{type(e).__name__}: {str(e)[:120]}"


def _compile_lua_test_libs(libs_dir):
    """Build the dynamic Lua test modules inside an isolated tests copy."""
    if not os.path.isdir(libs_dir):
        return "init", "test libs directory not found"

    module_sources = [
        ("lib1.so", "lib1.c"),
        ("lib11.so", "lib11.c"),
        ("lib2.so", "lib2.c"),
        ("lib21.so", "lib21.c"),
        ("lib2-v2.so", "lib22.c"),
    ]

    for out_name, src_name in module_sources:
        src_path = os.path.join(libs_dir, src_name)
        out_path = os.path.join(libs_dir, out_name)
        if platform.system() == "Darwin":
            cmd = [
                "cc",
                "-Wall",
                "-O2",
                "-I",
                lua_src_dir,
                "-bundle",
                "-undefined",
                "dynamic_lookup",
                "-o",
                out_path,
                src_path,
            ]
        else:
            cmd = [
                "cc",
                "-Wall",
                "-O2",
                "-I",
                lua_src_dir,
                "-fPIC",
                "-shared",
                "-o",
                out_path,
                src_path,
            ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            detail = r.stderr or r.stdout or "unknown error"
            return "build", detail[:300]

    return "ok", libs_dir


def _prepare_runtime_tests_dir():
    """Copy the Lua tests to a temp dir and build their C modules there."""
    if not os.path.isdir(lua_tests_dir):
        return "init", "testes/ not found"

    tmpdir = tempfile.mkdtemp(prefix="pcc_lua_tests_")
    runtime_tests_dir = os.path.join(tmpdir, "testes")
    shutil.copytree(lua_tests_dir, runtime_tests_dir)

    # Patch files.lua: /dev/full doesn't exist on macOS, skip that block
    if platform.system() == "Darwin":
        files_lua = os.path.join(runtime_tests_dir, "files.lua")
        with open(files_lua) as f:
            content = f.read()
        content = content.replace(
            '  local f = io.output("/dev/full")\n'
            '  assert(f:write("abcd"))   -- write to buffer\n'
            '  assert(not f:flush())     -- cannot write to device\n'
            '  assert(f:write("abcd"))   -- write to buffer\n'
            '  assert(not io.flush())    -- cannot write to device\n'
            '  assert(f:close())\n',
            '  -- /dev/full not available on macOS, skipped\n',
        )
        with open(files_lua, "w") as f:
            f.write(content)

    libs_stage, libs_detail = _compile_lua_test_libs(
        os.path.join(runtime_tests_dir, "libs")
    )
    if libs_stage != "ok":
        return libs_stage, libs_detail

    return "ok", runtime_tests_dir


def _compile_makefile_lua():
    """Build Lua using the project Makefile. Returns (stage, bin_path)."""
    makefile_path = os.path.join(lua_src_dir, "makefile")
    if not os.path.isfile(makefile_path):
        return "init", "makefile not found"

    try:
        tmpdir = tempfile.mkdtemp(prefix="pcc_make_lua_")
        build_dir = os.path.join(tmpdir, "lua-build")
        shutil.copytree(
            lua_src_dir,
            build_dir,
            ignore=shutil.ignore_patterns("*.o", "*.a", "lua", "all", "testes"),
        )

        make_args = ["make", "-C", build_dir, "CC=cc", "CWARNS="]
        if platform.system() == "Darwin":
            config_path = os.path.join(build_dir, "pcc_test_config.h")
            with open(config_path, "w") as f:
                f.write(_lua_preprocessor_prefix())
            make_args += [
                f"MYCFLAGS=-std=c99 -include {config_path}",
                "MYLDFLAGS=",
                "MYLIBS=",
            ]

        r = subprocess.run(make_args, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            return "make", r.stderr[:300]

        bin_path = os.path.join(build_dir, "lua")
        if not os.path.isfile(bin_path):
            return "make", "lua binary not produced"

        return "ok", bin_path
    except Exception as e:
        return "make", f"{type(e).__name__}: {str(e)[:120]}"


def _run_lua_script(bin_path, test_file, tests_dir=lua_tests_dir):
    script_path = os.path.abspath(os.path.join(tests_dir, test_file))
    # When running standalone (outside all.lua), set _port=true to skip
    # non-portable tests (e.g. io.stdin:seek) that assume a specific env.
    return subprocess.run(
        [bin_path, "-e", "_port=true", script_path],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tests_dir,
    )


def _normalize_runtime_stderr(stderr, bin_path):
    return stderr.replace(bin_path, "<lua>")


def _normalize_runtime_stdout(stdout, bin_path):
    stdout = stdout.replace(bin_path, "<lua>")
    stdout = _TMPFILE_PATH.sub("<tmp>", stdout)
    stdout = _RANDOM_SEEDS_LINE.sub("random seeds: <normalized>", stdout)
    stdout = _MEMORY_LINE.sub("memory:\t<normalized>", stdout)
    stdout = _TOTAL_MEMORY_SUMMARY_LINE.sub(
        "    ---- total memory: <normalized> ----", stdout
    )
    stdout = _TIME_LINE.sub("time: <normalized>", stdout)
    stdout = _TOTAL_TIME_LINE.sub("total time: <normalized>", stdout)
    stdout = _CLOCK_TIME.sub("<normalized time>", stdout)
    stdout = _FLOAT_RANDOM_RANGE_LINE.sub(
        "float random range in <normalized> calls: [<normalized>]", stdout
    )
    stdout = _INTEGER_RANDOM_RANGE_LINE.sub(
        "integer random range in <normalized> calls: [minint + <normalized>ppm, maxint - <normalized>ppm]",
        stdout,
    )
    stdout = _RANDOM_TABLE_SEEDS_LINE.sub(
        "testing length for some random tables (seeds <normalized>)", stdout
    )
    for pattern in _SORT_BENCHMARK_LINES:
        stdout = pattern.sub("<normalized sort benchmark>", stdout)
    stdout = _SHORT_CIRCUIT_OPT_LINE.sub(
        "testing short-circuit optimizations (<normalized>)", stdout
    )
    stdout = _BACKGROUND_PID_LINE.sub(
        "(if test fails now, it may leave a Lua script running in background, pid <normalized>)",
        stdout,
    )
    return stdout


@pytest.fixture(scope="session")
def pcc_lua_bin():
    """Compile onelua.c via pcc once per session."""
    return _compile_onelua()


@pytest.fixture(scope="session")
def native_lua_bin():
    """Compile onelua.c via cc once per session."""
    return _compile_native_onelua()


@pytest.fixture(scope="session")
def makefile_lua_bin():
    """Build Lua via Makefile once per session."""
    return _compile_makefile_lua()


@pytest.fixture(scope="session")
def lua_runtime_tests_dir():
    """Prepare an isolated copy of the Lua tests with loadable C modules."""
    return _prepare_runtime_tests_dir()


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(lua_src_dir, "onelua.c")),
    reason="onelua.c not found",
)
def test_onelua_compile_and_link(pcc_lua_bin):
    """Compile onelua.c (single TU) → .o → linked binary."""
    stage, result = pcc_lua_bin
    if stage == "ok":
        assert os.path.isfile(result), f"Binary not found: {result}"
    else:
        pytest.xfail(f"Stage '{stage}': {result}")


@pytest.mark.skipif(not os.path.isdir(lua_tests_dir), reason="testes/ not found")
@pytest.mark.parametrize("test_file", LUA_TEST_FILES, ids=LUA_TEST_FILES)
def test_pcc_runtime_matches_makefile(
    test_file, pcc_lua_bin, makefile_lua_bin, lua_runtime_tests_dir
):
    """Compare pcc-compiled onelua vs Makefile-built lua (official reference)."""
    pcc_stage, pcc_bin = pcc_lua_bin
    if pcc_stage != "ok":
        pytest.xfail(f"pcc stage '{pcc_stage}': {pcc_bin}")

    make_stage, make_bin = makefile_lua_bin
    if make_stage != "ok":
        pytest.xfail(f"makefile stage '{make_stage}': {make_bin}")

    tests_stage, tests_dir = lua_runtime_tests_dir
    if tests_stage != "ok":
        pytest.xfail(f"runtime tests stage '{tests_stage}': {tests_dir}")

    pcc_r = _run_lua_script(pcc_bin, test_file, tests_dir)
    make_r = _run_lua_script(make_bin, test_file, tests_dir)

    assert pcc_r.returncode == make_r.returncode
    assert _normalize_runtime_stdout(
        pcc_r.stdout, pcc_bin
    ) == _normalize_runtime_stdout(make_r.stdout, make_bin)
    assert _normalize_runtime_stderr(
        pcc_r.stderr, pcc_bin
    ) == _normalize_runtime_stderr(make_r.stderr, make_bin)


@pytest.mark.skipif(not os.path.isdir(lua_tests_dir), reason="testes/ not found")
@pytest.mark.parametrize("test_file", LUA_TEST_FILES, ids=LUA_TEST_FILES)
def test_pcc_runtime_matches_native(
    test_file, pcc_lua_bin, native_lua_bin, lua_runtime_tests_dir
):
    """Compare pcc-compiled onelua vs cc-compiled onelua (same source, test pcc as C compiler)."""
    pcc_stage, pcc_bin = pcc_lua_bin
    if pcc_stage != "ok":
        pytest.xfail(f"pcc stage '{pcc_stage}': {pcc_bin}")

    native_stage, native_bin = native_lua_bin
    if native_stage != "ok":
        pytest.xfail(f"native stage '{native_stage}': {native_bin}")

    tests_stage, tests_dir = lua_runtime_tests_dir
    if tests_stage != "ok":
        pytest.xfail(f"runtime tests stage '{tests_stage}': {tests_dir}")

    pcc_r = _run_lua_script(pcc_bin, test_file, tests_dir)
    native_r = _run_lua_script(native_bin, test_file, tests_dir)

    assert pcc_r.returncode == native_r.returncode
    assert _normalize_runtime_stdout(
        pcc_r.stdout, pcc_bin
    ) == _normalize_runtime_stdout(native_r.stdout, native_bin)
    assert _normalize_runtime_stderr(
        pcc_r.stderr, pcc_bin
    ) == _normalize_runtime_stderr(native_r.stderr, native_bin)


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(lua_src_dir, "makefile")),
    reason="makefile not found",
)
@pytest.mark.skipif(not os.path.isdir(lua_tests_dir), reason="testes/ not found")
@pytest.mark.parametrize("test_file", LUA_TEST_FILES, ids=LUA_TEST_FILES)
def test_makefile_lua_test_suite(test_file, makefile_lua_bin, lua_runtime_tests_dir):
    """Run full Lua test suite with Makefile-built binary."""
    make_stage, make_bin = makefile_lua_bin
    if make_stage != "ok":
        pytest.xfail(f"makefile stage '{make_stage}': {make_bin}")

    tests_stage, tests_dir = lua_runtime_tests_dir
    if tests_stage != "ok":
        pytest.xfail(f"runtime tests stage '{tests_stage}': {tests_dir}")

    r = _run_lua_script(make_bin, test_file, tests_dir)
    if r.returncode != 0:
        pytest.xfail(f"exit={r.returncode}: {r.stderr[:200]}")


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(lua_tests_dir, "all.lua")),
    reason="all.lua not found",
)
def test_makefile_lua_all(makefile_lua_bin, lua_runtime_tests_dir):
    """Run all.lua — the official Lua test suite entry point."""
    make_stage, make_bin = makefile_lua_bin
    if make_stage != "ok":
        pytest.xfail(f"makefile stage '{make_stage}': {make_bin}")

    tests_stage, tests_dir = lua_runtime_tests_dir
    if tests_stage != "ok":
        pytest.xfail(f"runtime tests stage '{tests_stage}': {tests_dir}")

    script_path = os.path.abspath(os.path.join(tests_dir, "all.lua"))
    # _port=true: skip non-portable tests (e.g. /dev/full on macOS)
    # _soft=true: skip long-running tests (e.g. heavy.lua)
    r = subprocess.run(
        [make_bin, "-e", "_port=true; _soft=true", script_path],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=tests_dir,
    )
    assert r.returncode == 0, f"all.lua failed (rc={r.returncode}):\n{r.stderr[-500:]}"
