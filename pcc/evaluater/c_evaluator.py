import llvmlite.ir as ir
import llvmlite.binding as llvm
import os
import subprocess
import shutil
import sys
from ..codegen.c_codegen import LLVMCodeGenerator, postprocess_ir_text
from ..parse.c_parser import CParser
from ..preprocessor import preprocess

from ctypes import (
    CFUNCTYPE,
    c_double,
    c_int64,
    c_int32,
    c_int16,
    c_int8,
    c_char_p,
    POINTER,
)


def get_c_type_from_ir(ir_type):
    if isinstance(ir_type, ir.VoidType):
        return None
    elif isinstance(ir_type, ir.IntType):
        if ir_type.width == 8:
            return c_int8
        elif ir_type.width == 16:
            return c_int16
        elif ir_type.width == 32:
            return c_int32
        return c_int64
    elif isinstance(ir_type, ir.DoubleType):
        return c_double
    elif isinstance(ir_type, ir.PointerType):
        point_type = get_c_type_from_ir(ir_type.pointee)
        return POINTER(point_type)
    else:
        return c_int64


class CEvaluator(object):

    def __init__(self):

        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()

        self.codegen = LLVMCodeGenerator()
        self.parser = CParser()
        self.target = llvm.Target.from_default_triple()
        self.ee = None

    def evaluate(
        self,
        codestr,
        optimize=True,
        llvmdump=False,
        args=None,
        base_dir=None,
        use_system_cpp=None,
        prog_args=None,
    ):
        if use_system_cpp is None:
            use_system_cpp = self._has_system_cpp()
        if use_system_cpp:
            codestr = self._system_cpp(codestr, base_dir)
            import re

            codestr = re.sub(
                r"typedef\s+(int|char|short|long|double|float|void)\s+\1\s*;",
                "",
                codestr,
            )
        else:
            codestr = preprocess(codestr, base_dir=base_dir)
        ast = self.parser.parse(codestr)
        self.codegen = LLVMCodeGenerator()
        self.codegen.generate_code(ast)
        ir_text = postprocess_ir_text(str(self.codegen.module))

        if llvmdump:
            with open("temp.ir", "w") as f:
                f.write(ir_text)

        llvmmod = llvm.parse_assembly(ir_text)

        if optimize:
            target_machine = self.target.create_target_machine()
            pto = llvm.create_pipeline_tuning_options(speed_level=2, size_level=0)
            pb = llvm.create_pass_builder(target_machine, pto)
            pm = pb.getModulePassManager()
            pm.run(llvmmod, pb)

            if llvmdump:
                tempbcode = str(llvmmod)
                with open("temp.ooptimize.bcode", "w") as f:
                    f.write(tempbcode)

        target_machine = self.target.create_target_machine()

        self.ee = llvm.create_mcjit_compiler(llvmmod, target_machine)
        self.ee.finalize_object()

        if llvmdump:
            tempbcode = target_machine.emit_assembly(llvmmod)
            with open("temp.bcode", "w") as f:
                f.write(tempbcode)

        return_type = get_c_type_from_ir(self.codegen.return_type)

        main_addr = self.ee.get_function_address("main")

        if prog_args:
            # Build argc/argv for main(int argc, char **argv)
            argv_strings = ["pcc"] + list(prog_args)
            argc = len(argv_strings)
            ArgvType = c_char_p * (argc + 1)
            argv = ArgvType(*[s.encode() for s in argv_strings], None)
            fptr = CFUNCTYPE(return_type, c_int32, POINTER(c_char_p))(main_addr)
            result = fptr(argc, argv)
        else:
            fptr = CFUNCTYPE(return_type)(main_addr)
            if args is None:
                args = []
            result = fptr(*args)

        return result

    @staticmethod
    def _has_system_cpp():
        return shutil.which("cc") is not None or shutil.which("gcc") is not None

    @staticmethod
    def _system_cpp(source, base_dir=None):
        """Use system C preprocessor (cc -E) for fast preprocessing.

        Uses -nostdinc + fake libc headers so output is pycparser-compatible.
        """
        import tempfile

        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if not cc:
            raise RuntimeError("No system C compiler found for preprocessing")

        # Find fake libc headers (shipped with pcc)
        # __file__ = pcc/evaluater/c_evaluator.py → project root is 2 levels up
        pcc_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        fake_libc = os.path.join(pcc_root, "utils", "fake_libc_include")
        base_dir = os.path.abspath(base_dir) if base_dir else os.getcwd()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".c", dir=base_dir, delete=False
        ) as f:
            f.write(source)
            tmp_path = f.name
        try:
            platform_defs = []
            if sys.platform == "darwin":
                # fake libc headers do not provide the stdio macro remaps that
                # macOS uses for FILE* globals.
                platform_defs.extend(
                    [
                        "-Dstdin=__stdinp",
                        "-Dstdout=__stdoutp",
                        "-Dstderr=__stderrp",
                    ]
                )

            cmd = [
                cc,
                "-E",
                "-P",
                "-nostdinc",  # skip real system headers
                "-isystem",
                fake_libc,  # fake libc as system headers
                "-I",
                base_dir or ".",  # project headers
                # Standard limits (fake headers don't have these)
                "-DLLONG_MAX=9223372036854775807LL",
                "-DLLONG_MIN=(-9223372036854775807LL-1)",
                "-DULLONG_MAX=18446744073709551615ULL",
                "-DLONG_MAX=9223372036854775807L",
                "-DINT_MAX=2147483647",
                "-DINT_MIN=(-2147483647-1)",
                "-DLONG_MIN=(-9223372036854775807L-1)",
                "-DUINT_MAX=4294967295U",
                "-DCHAR_BIT=8",
                "-DSHRT_MAX=32767",
                "-DUSHRT_MAX=65535",
                "-DCHAR_MAX=127",
                "-DUCHAR_MAX=255",
                "-DSIG_DFL=0",
                "-DSIG_IGN=1",
                "-DSIGINT=2",
                "-DCLOCKS_PER_SEC=1000000",
                "-DLC_ALL=0",
                "-DLC_COLLATE=1",
                "-DLC_CTYPE=2",
                "-DLC_MONETARY=3",
                "-DLC_NUMERIC=4",
                "-DLC_TIME=5",
                "-Doffsetof(t,m)=((long)&((t*)0)->m)",
                "-DDBL_MANT_DIG=53",
                "-DFLT_MANT_DIG=24",
                "-DDBL_MAX_EXP=1024",
                "-DFLT_MAX_EXP=128",
                "-DDBL_MAX=1.7976931348623158e+308",
                "-DHUGE_VAL=1e309",
                "-DHUGE_VALF=1e39f",
                "-DDBL_MAX_10_EXP=308",
                "-DFLT_MAX_10_EXP=38",
                "-DDBL_MIN_EXP=-1021",
                "-DDBL_EPSILON=2.2204460492503131e-16",
                "-D_IONBF=2",
                "-D_IOLBF=1",
                "-D_IOFBF=0",
                # GCC/Clang extensions
                "-D__attribute__(x)=",
                "-D__extension__=",
                # Disable Lua's computed goto and builtins
                "-DLUA_USE_JUMPTABLE=0",
                "-DLUA_NOBUILTIN",
                *platform_defs,
                tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return result.stdout
            # Fallback: without -nostdinc
            cmd = [cc, "-E", "-P", "-I", base_dir or ".", tmp_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.stdout
        finally:
            os.unlink(tmp_path)
