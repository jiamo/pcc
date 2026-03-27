import os
import sys
import subprocess
import tempfile

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.parse.c_parser import CParser
from pcc.codegen.c_codegen import LLVMCodeGenerator, postprocess_ir_text
from pcc.project import TranslationUnit


def test_stdarg_pointer_int_double_roundtrip():
    source = r"""
        #include <stdarg.h>

        int check(const char *fmt, ...) {
            va_list ap;
            va_start(ap, fmt);
            char *s = va_arg(ap, char *);
            int i = va_arg(ap, int);
            double d = va_arg(ap, double);
            void *p = va_arg(ap, void *);
            va_end(ap);
            return (s[0] == 'h' && s[1] == 'i' &&
                    i == 42 &&
                    d > 2.4 && d < 2.6 &&
                    p == s) ? 0 : 1;
        }

        int main() {
            char *s = "hi";
            return check("", s, 42, 2.5, s);
        }
    """

    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)

    with tempfile.TemporaryDirectory(prefix="pcc_vararg_") as tmpdir:
        ir_path = os.path.join(tmpdir, "vararg.ll")
        obj_path = os.path.join(tmpdir, "vararg.o")
        bin_path = os.path.join(tmpdir, "vararg_bin")

        with open(ir_path, "w") as f:
            f.write(postprocess_ir_text(str(cg.module)))

        r = subprocess.run(
            ["cc", "-c", "-w", ir_path, "-o", obj_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run(
            ["cc", obj_path, "-o", bin_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_stdarg_helper_accepts_va_list_parameter():
    source = r"""
        #include <stdarg.h>

        int collect_from_list(va_list ap) {
            char *s = va_arg(ap, char *);
            int i = va_arg(ap, int);
            double d = va_arg(ap, double);
            void *p = va_arg(ap, void *);
            return (s[0] == 'o' && s[1] == 'k' &&
                    i == 7 &&
                    d > 1.2 && d < 1.3 &&
                    p == s) ? 0 : 1;
        }

        int check(const char *fmt, ...) {
            va_list ap;
            int rc;
            va_start(ap, fmt);
            rc = collect_from_list(ap);
            va_end(ap);
            return rc;
        }

        int main() {
            char *s = "ok";
            return check("", s, 7, 1.25, s);
        }
    """

    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)

    with tempfile.TemporaryDirectory(prefix="pcc_vararg_") as tmpdir:
        ir_path = os.path.join(tmpdir, "vararg.ll")
        obj_path = os.path.join(tmpdir, "vararg.o")
        bin_path = os.path.join(tmpdir, "vararg_bin")

        with open(ir_path, "w") as f:
            f.write(postprocess_ir_text(str(cg.module)))

        r = subprocess.run(
            ["cc", "-c", "-w", ir_path, "-o", obj_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run(
            ["cc", obj_path, "-o", bin_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_variadic_string_literal_argument_decays_to_pointer():
    source = r"""
        #include <stdarg.h>

        int check(const char *fmt, ...) {
            va_list ap;
            char *first;
            char *second;
            va_start(ap, fmt);
            first = va_arg(ap, char *);
            second = va_arg(ap, char *);
            va_end(ap);
            return (first[0] == 'o' && first[1] == 'k' &&
                    second[0] == 'x' && second[1] == 'y' &&
                    second[2] == 0) ? 0 : 1;
        }

        int main() {
            char *s = "ok";
            return check("%s%s", s, "xy");
        }
    """

    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)

    with tempfile.TemporaryDirectory(prefix="pcc_vararg_") as tmpdir:
        ir_path = os.path.join(tmpdir, "vararg.ll")
        obj_path = os.path.join(tmpdir, "vararg.o")
        bin_path = os.path.join(tmpdir, "vararg_bin")

        with open(ir_path, "w") as f:
            f.write(postprocess_ir_text(str(cg.module)))

        r = subprocess.run(
            ["cc", "-c", "-w", ir_path, "-o", obj_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run(
            ["cc", obj_path, "-o", bin_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_direct_builtin_va_start_and_va_copy_work_with_system_style_va_list():
    code = r'''
        typedef char *__builtin_va_list;
        typedef __builtin_va_list va_list;

        int vsnprintf(char *s, unsigned long n, const char *format, va_list ap);
        int strcmp(const char *a, const char *b);

        int check(const char *fmt, ...) {
            char buf[32];
            int n;
            va_list args;
            va_list copy;

            __builtin_va_start(args, fmt);
            __builtin_va_copy(copy, args);
            n = vsnprintf(buf, 32, fmt, copy);
            __builtin_va_end(copy);
            __builtin_va_end(args);

            return (n == 6 && strcmp(buf, "num=42") == 0) ? 0 : 1;
        }

        int main(void) {
            return check("num=%d", 42);
        }
    '''

    unit = TranslationUnit(
        name="direct_builtin_va_start.c",
        path=os.path.join(parent_dir, "direct_builtin_va_start.c"),
        source=code,
    )

    result = CEvaluator().run_translation_units_with_system_cc(
        [unit],
        optimize=True,
        base_dir=parent_dir,
        jobs=1,
        include_dirs=[],
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cast_variadic_function_pointer_keeps_varargs_and_promotions():
    pcc = CEvaluator()
    ret = pcc.evaluate(
        r'''
        #include <stdarg.h>

        typedef long (*generic_fn)(void);

        long collect(int tag, ...) {
            va_list ap;
            int promoted_char;
            double promoted_float;
            char *text;
            va_start(ap, tag);
            promoted_char = va_arg(ap, int);
            promoted_float = va_arg(ap, double);
            text = va_arg(ap, char *);
            va_end(ap);
            return (promoted_char == 65 &&
                    promoted_float > 2.4 &&
                    promoted_float < 2.6 &&
                    text[0] == 'o' &&
                    text[1] == 'k' &&
                    text[2] == 0) ? 0 : 1;
        }

        int main(void) {
            generic_fn raw = (generic_fn)collect;
            char ch = 'A';
            float f = 2.5f;
            char *text = "ok";
            return ((long (*)(int, ...))raw)(0, ch, f, text);
        }
        ''',
        optimize=False,
    )
    assert ret == 0


def test_sqlite_style_variadic_function_pointer_table_passes_pointer_args():
    code = r'''
        #include <fcntl.h>
        #include <unistd.h>
        #include <errno.h>

        typedef void (*syscall_ptr)(void);

        struct Entry {
            const char *name;
            syscall_ptr current;
        };

        static struct Entry table[] = {
            { "open", (syscall_ptr)open },
            { "close", (syscall_ptr)close },
            { "fcntl", (syscall_ptr)fcntl },
        };

        #define osOpen ((int(*)(const char*,int,int))table[0].current)
        #define osClose ((int(*)(int))table[1].current)
        #define osFcntl ((int(*)(int,int,...))table[2].current)

        int main(int argc, char **argv) {
            int fd = osOpen(argv[1], O_RDWR | O_CREAT, 0600);
            struct flock lock;
            if (fd < 0) {
                return errno ? errno : 100;
            }
            lock.l_start = 0;
            lock.l_len = 1;
            lock.l_pid = 0;
            lock.l_type = F_RDLCK;
            lock.l_whence = 0;
            if (osFcntl(fd, F_GETLK, &lock) != 0) {
                int err = errno;
                osClose(fd);
                return err ? err : 101;
            }
            osClose(fd);
            return 0;
        }
    '''

    unit = TranslationUnit(
        name="sqlite_style_variadic_fp.c",
        path=os.path.join(parent_dir, "sqlite_style_variadic_fp.c"),
        source=code,
    )

    fd, path = tempfile.mkstemp(prefix="pcc_sqlite_style_", suffix=".db")
    os.close(fd)
    os.unlink(path)
    try:
        result = CEvaluator().run_translation_units_with_system_cc(
            [unit],
            optimize=True,
            base_dir=parent_dir,
            prog_args=[path],
            include_dirs=[],
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        if os.path.exists(path):
            os.unlink(path)
