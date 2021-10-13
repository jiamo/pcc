import llvmlite.ir as ir
import llvmlite.binding as llvm
from ..codegen.c_codegen import LLVMCodeGenerator
from ..parse.c_parser import CParser

from ctypes import CFUNCTYPE, c_double, c_int64, POINTER


def get_c_type_from_ir(ir_type):
    if isinstance(ir_type, ir.IntType):
        return_type = c_int64
    elif isinstance(ir_type, ir.DoubleType):
        return_type = c_double
    elif isinstance(ir_type, ir.PointerType):
        point_type = get_c_type_from_ir(ir_type.pointee)
        return_type = POINTER(point_type)
    else:
        return_type = c_int64

    return return_type


class CEvaluator(object):

    def __init__(self):

        llvm.initialize()
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()

        self.codegen = LLVMCodeGenerator()
        self.parser = CParser()
        self.target = llvm.Target.from_default_triple()
        self.ee = None

    def evaluate(self, codestr, optimize=True, llvmdump=False, args=None):
        ast = self.parser.parse(codestr)
        self.codegen.generate_code(ast)

        if llvmdump:
            tempstr = str(self.codegen.module)
            with(open("temp.ir", "w")) as f:
                f.write(tempstr)

        print(str(self.codegen.module))
        llvmmod = llvm.parse_assembly(str(self.codegen.module))

        if optimize:
            pmb = llvm.create_pass_manager_builder()
            pmb.opt_level = 2
            pm = llvm.create_module_pass_manager()
            pmb.populate(pm)
            pm.run(llvmmod)

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
