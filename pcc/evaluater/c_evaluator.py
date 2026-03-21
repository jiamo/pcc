import llvmlite.ir as ir
import llvmlite.binding as llvm
from ..codegen.c_codegen import LLVMCodeGenerator
from ..parse.c_parser import CParser

from ctypes import CFUNCTYPE, c_double, c_int64, c_int8, POINTER


def get_c_type_from_ir(ir_type):
    if isinstance(ir_type, ir.VoidType):
        return None
    elif isinstance(ir_type, ir.IntType):
        if ir_type.width == 8:
            return c_int8
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

    def evaluate(self, codestr, optimize=True, llvmdump=False, args=None):
        ast = self.parser.parse(codestr)
        self.codegen = LLVMCodeGenerator()
        self.codegen.generate_code(ast)

        if llvmdump:
            tempstr = str(self.codegen.module)
            with(open("temp.ir", "w")) as f:
                f.write(tempstr)

        llvmmod = llvm.parse_assembly(str(self.codegen.module))

        if optimize:
            target_machine = self.target.create_target_machine()
            pto = llvm.create_pipeline_tuning_options(speed_level=2, size_level=0)
            pb = llvm.create_pass_builder(target_machine, pto)
            pm = pb.getModulePassManager()
            pm.run(llvmmod, pb)

            if llvmdump:
                tempbcode = str(llvmmod)
                with(open("temp.ooptimize.bcode", "w")) as f:
                    f.write(tempbcode)

        target_machine = self.target.create_target_machine()

        self.ee = llvm.create_mcjit_compiler(llvmmod, target_machine)
        self.ee.finalize_object()

        if llvmdump:
            tempbcode = target_machine.emit_assembly(llvmmod)
            with(open("temp.bcode", "w")) as f:
                f.write(tempbcode)

        return_type = get_c_type_from_ir(self.codegen.return_type)

        # how to get main args type
        fptr = CFUNCTYPE(return_type)(self.ee.get_function_address("main"))
        #
        if args is None:
            args = []
        result = fptr(*args)

        return result
