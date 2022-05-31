import llvmlite.ir as ir
from collections import ChainMap
from contextlib import contextmanager
from llvmlite.ir import IRBuilder
from ..ast import c_ast as c_ast

bool_t = ir.IntType(1)
int8_t = ir.IntType(8)
int32_t = ir.IntType(32)
int64_t = ir.IntType(64)
voidptr_t = int8_t.as_pointer()
int64ptr_t = int64_t.as_pointer()
true_bit = bool_t(1)
false_bit = bool_t(0)
true_byte = int8_t(1)
false_byte = int8_t(0)
cstring = voidptr_t


class CodegenError(Exception):
    pass


def get_ir_type(type_str):
    # only support int and double
    if type_str == "int":
        ir_type = int64_t
    else:
        ir_type = ir.DoubleType()
    return ir_type


def get_ir_type_from_node(node):

    if isinstance(node.type, c_ast.PtrDecl):
        # only support one level ptr just for simple use
        return_type_str = node.type.type.type.names[0]
        if return_type_str == "int":
            data_ir_type = ir.IntType(64)
        else:
            data_ir_type = ir.DoubleType()
        ir_type = ir.PointerType(data_ir_type)

    else:
        return_type_str = node.type.type.names[0]
        if return_type_str == "int":
            ir_type = ir.IntType(64)
        else:
            ir_type = ir.DoubleType()
    return ir_type


class LLVMCodeGenerator(object):

    def __init__(self):
        self.module = ir.Module()

        #
        self.builder = None
        self.func_symtab = {}
        self.func_tyinfo = {}
        self.global_symtab = {}
        self.global_tyinfo = {}
        self.global_builder:IRBuilder = ir.IRBuilder()
        self.in_builder = None
        self.env = ChainMap()
        self.nlabels = 0
        self.function = None
        self.in_global = True
        fnty = ir.FunctionType(int32_t, [cstring], var_arg=True)
        callee_func = ir.Function(self.module, fnty, name="printf")
        fnty1 = ir.FunctionType(int64ptr_t, [int64_t], var_arg=True)
        callee_func1 = ir.Function(self.module, fnty1, name="malloc")
        self.define('printf', (fnty, callee_func))
        self.define('malloc', (fnty1, callee_func1))

    def define(self, name, val):
        self.env[name] = val

    def lookup(self, name):
        return self.env[name]

    def new_label(self, name):
        self.nlabels += 1
        return f'label_{name}_{self.nlabels}'

    @contextmanager
    def new_scope(self):
        self.env = self.env.new_child()
        yield
        self.env = self.env.parents

    @contextmanager
    def new_function(self):
        oldfunc = self.function
        oldbuilder = self.builder
        self.in_global = False
        try:
            yield
        finally:
            self.function = oldfunc
            self.builder = oldbuilder
            self.in_global = True

    def generate_code(self, node):
        normal = self.codegen(node)

        # for else end have no instruction
        if self.builder:
            if not self.builder.block.is_terminated:
                self.builder.ret(ir.Constant(ir.IntType(64), int(0)))
        return normal

    def create_entry_block_alloca(
            self, name, type_str, size, array_list=None, point_level=0):

        ir_type = None

        if type_str == "int":
            ir_type = ir.IntType(64)

        elif type_str == "double":
            ir_type = ir.DoubleType()

        if array_list is not None:
            reversed_list = reversed(array_list)
            for dim in reversed_list:
                ir_type = ir.ArrayType(ir_type, dim)
            ir_type.dim_array = array_list

        if point_level != 0:
            for level in range(point_level):
                ir_type = ir.PointerType(ir_type)

        if not self.in_global:
            ret = self.builder.alloca(ir_type, size=None, name=name)
            self.define(name, (ir_type, ret))
        else:
            ret = ir.GlobalVariable(self.module, ir_type, name)
            self.define(name, (ir_type, ret))

        return ret, ir_type

    def codegen(self, node):
        method = 'codegen_' + node.__class__.__name__
        return getattr(self, method)(node)

    def codegen_FileAST(self, node):
        for ext in node.ext:
            self.codegen(ext)

    def codegen_NumberExprAST(self, node):
        return ir.values.Constant(ir.DoubleType(), float(node.val)), None

    def codegen_Constant(self, node):
        node.show()
        if node.type == "int":
            return ir.values.Constant(ir.IntType(64), int(node.value)), None
        elif node.type == 'string':

            b = bytearray(node.value[1:-1] + '\00', encoding='ascii')
            n = len(b)
            array = ir.ArrayType(ir.IntType(8), n)
            tmp = ir.values.Constant(
                array,
                b)
            return tmp , None
        else:
            return ir.values.Constant(ir.DoubleType(), float(node.value)), None

    def codegen_Assignment(self, node):
        node.show()
        lv, lv_addr = self.codegen(node.lvalue)
        rv, _ = self.codegen(node.rvalue)
        result = None

        dispatch_type_double = 1
        dispatch_type_int = 0
        dispatch_dict = {
            ("+=", dispatch_type_double): self.builder.fadd,
            ("+=", dispatch_type_int): self.builder.add,
            ("-=", dispatch_type_double): self.builder.fsub,
            ("-=", dispatch_type_int): self.builder.sub,
        }
        if isinstance(lv.type, ir.IntType) and isinstance(rv.type, ir.IntType):
            dispatch_type = dispatch_type_int
        else:
            dispatch_type = dispatch_type_double
        dispatch = (node.op, dispatch_type)
        handle = dispatch_dict.get(dispatch)

        if node.op == '=':
            result = self.builder.store(rv, lv_addr)
        else:
            addresult = handle(lv, rv, 'addtmp')
            result = self.builder.store(addresult, lv_addr)

        return result, None

    def codegen_VariableExprAST(self, node):
        var_addr = self.func_symtab[node.name]
        return self.builder.load(var_addr, node.name), None

    def codegen_UnaryExprAST(self, node):
        operand, _ = self.codegen(node.operand)
        func = self.module.globals.get('unary{0}'.format(node.op))
        return self.builder.call(func, [operand], 'unop'), None

    def codegen_UnaryOp(self, node):
        node.show()
        result = None
        result_ptr = None

        # import pdb;pdb.set_trace()
        # TODO at now just support int ++
        if node.op == "p++":
            _, lv_addr = self.lookup(node.expr.name)
            lv = self.builder.load(lv_addr, node.expr.name)
            rv = ir.Constant(ir.IntType(64), 1)
            addresult = self.builder.add(lv, rv, 'addtmp')
            result = self.builder.store(addresult, lv_addr), None

        if node.op == "p--":
            _, lv_addr = self.lookup(node.expr.name)
            lv = self.builder.load(lv_addr, node.expr.name)
            rv = ir.Constant(ir.IntType(64), 1)
            addresult = self.builder.sub(lv, rv, 'addtmp')
            result = self.builder.store(addresult, lv_addr)

        if node.op == '*':
            name_ir, name_ptr = self.codegen(node.expr)
            # result_ptr = self.builder.load(name_ptr)
            result_ptr = name_ir
            result = self.builder.load(result_ptr)

        if node.op == '&':
            name_ir, name_ptr = self.codegen(node.expr)
            result_ptr = name_ptr
            result = result_ptr  # got point from value is the result

        return result, result_ptr

    def codegen_BinaryOp(self, node):
        lhs, _ = self.codegen(node.left)
        rhs, _ = self.codegen(node.right)
        # import pdb;pdb.set_trace()
        dispatch_type_double = 1
        dispatch_type_int = 0
        dispatch_dict = {
            ("+", dispatch_type_double): self.builder.fadd,
            ("+", dispatch_type_int): self.builder.add,
            ("-", dispatch_type_double): self.builder.fsub,
            ("-", dispatch_type_int): self.builder.sub,
            ("*", dispatch_type_double): self.builder.fmul,
            ("*", dispatch_type_int): self.builder.mul,
        }
        if isinstance(lhs.type, ir.IntType) and isinstance(rhs.type,
                                                           ir.IntType):
            dispatch_type = dispatch_type_int
        else:
            dispatch_type = dispatch_type_double
        dispatch = (node.op, dispatch_type)
        handle = dispatch_dict.get(dispatch)
        # import pdb;pdb.set_trace()
        if node.op in ['+', '-', '*']:
            return handle(lhs, rhs, 'tmp'), None
        elif node.op in [">", "<", ">=", "<=", "!=", "=="]:
            if dispatch_type == dispatch_type_int:
                cmp = self.builder.icmp_signed(node.op, lhs, rhs, 'cmptmp')
                return self.builder.uitofp(cmp, ir.DoubleType(),
                                           'booltmp'), None
            else:
                cmp = self.builder.fcmp_unordered(node.op, lhs, rhs, 'cmptmp')
                return self.builder.uitofp(cmp, ir.DoubleType(),
                                           'booltmp'), None
        else:
            func = self.module.globals.get('binary{0}'.format(node.op))
            return self.builder.call(func, [lhs, rhs], 'binop'), None

    def codegen_If(self, node):

        node.show()
        cond_val, _ = self.codegen(node.cond)
        cmp = self.builder.fcmp_ordered(
            '!=', cond_val, ir.Constant(ir.DoubleType(), 0.0))

        then_bb = self.builder.function.append_basic_block('then')
        else_bb = self.builder.function.append_basic_block('else')
        merge_bb = self.builder.function.append_basic_block('ifend')

        self.builder.cbranch(cmp, then_bb, else_bb)

        with self.new_scope():
            self.builder.position_at_end(then_bb)
            then_val, _ = self.codegen(node.iftrue)
            # while true {
            #   n = n + 1;
            #   if n == 5 {
            #   continue;   # we cant't reach after continue
            #   }
            if not then_bb.is_terminated:
                self.builder.branch(merge_bb)

        with self.new_scope():
            self.builder.position_at_end(else_bb)
            if node.iffalse:
                elseval, _ = self.codegen(node.iffalse)
            if not else_bb.is_terminated:
                self.builder.branch(merge_bb)
            # context.builder.branch(merge)
        self.builder.position_at_end(merge_bb)  # begin at end to generate code
        # self.builder.block = merge_bb

        return None, None

    def codegen_NoneType(self, node):
        return None, None

    def codegen_For(self, node):

        saved_block = self.builder.block
        self.builder.position_at_end(
            saved_block)  # why the save_block at the end

        start_val, _ = self.codegen(node.init)

        # The builder is what? loop is a block which begin with loop
        test_bb = self.builder.function.append_basic_block('test')
        loop_bb = self.builder.function.append_basic_block('loop')
        next_bb = self.builder.function.append_basic_block('next')

        # append by name nor just add it
        after_loop_label = self.new_label("afterloop")
        after_bb = ir.Block(self.builder.function, after_loop_label)
        # self.builder.function.append_basic_block('afterloop')

        self.builder.branch(test_bb)
        self.builder.position_at_end(test_bb)

        endcond, _ = self.codegen(node.cond)
        cmp = self.builder.fcmp_ordered(
            '!=', endcond, ir.values.Constant(ir.DoubleType(), 0.0),
            'loopcond')
        self.builder.cbranch(cmp, loop_bb, after_bb)

        with self.new_scope():
            self.define('break', after_bb)
            self.define('continue', next_bb)
            self.builder.position_at_end(loop_bb)
            body_val, _ = self.codegen(node.stmt)  # if was ready codegen
            self.builder.branch(next_bb)
            self.builder.position_at_end(next_bb)
            self.codegen(node.next)
            self.builder.branch(test_bb)
        # this append_basic_blook change the label
        # after_bb = self.builder.function.append_basic_block(after_loop_label)
        self.builder.function.basic_blocks.append(after_bb)
        self.builder.position_at_end(after_bb)

        return ir.values.Constant(ir.DoubleType(), 0.0), None

    def codegen_While(self, node):

        node.show()
        saved_block = self.builder.block
        id_name = node.__class__.__name__
        self.builder.position_at_end(saved_block)
        # The builder is what? loop is a block which begin with loop
        test_bb = self.builder.function.append_basic_block('test')  # just create some block need to be filled
        loop_bb = self.builder.function.append_basic_block('loop')
        after_bb = self.builder.function.append_basic_block('afterloop')


        self.builder.branch(test_bb)
        self.builder.position_at_start(test_bb)
        endcond, _ = self.codegen(node.cond)
        cmp = self.builder.fcmp_ordered(
            '!=', endcond, ir.values.Constant(ir.DoubleType(), 0.0),
            'loopcond')
        self.builder.cbranch(cmp, loop_bb, after_bb)

        with self.new_scope():
            self.define('break', after_bb)
            self.define('continue', test_bb)
            self.builder.position_at_end(loop_bb)
            body_val, _ = self.codegen(node.stmt)
            # after eval body we need to goto test_bb
            # New code will be inserted into after_bb
            self.builder.branch(test_bb)
            self.builder.position_at_end(after_bb)

        # The 'for' expression always returns 0
        return ir.values.Constant(ir.DoubleType(), 0.0)


    def codegen_Break(self, node):
        self.builder.branch(self.lookup('break'))

    def codegen_Continue(self, node):
        self.builder.branch(self.lookup('continue'))

    def codegen_Cast(self, node):

        node.show()
        expr, ptr = self.codegen(node.expr)
        dest_type_str = node.to_type.type.type.names[0]
        match (type(expr.type), dest_type_str):
            # ir.types different from ir.IntType
            case (ir.types.DoubleType, "int"):
                return self.builder.fptosi(expr, int64_t), None


    def codegen_FuncCall(self, node):
        node.show()
        callee = None

        if isinstance(node.name, c_ast.ID):
            callee = node.name.name

        _, callee_func = self.lookup(callee)

        call_args = []
        if node.args:
            call_args = [self.codegen(arg)[0] for arg in node.args.exprs]

        # just for see and hard code it
        if callee == "printf":
            data_fmt = call_args[0]
            global_fmt = ir.GlobalVariable(
                self.module, data_fmt.type, "printf_format")
            global_fmt.initializer = data_fmt
            format_ptr = self.builder.bitcast(global_fmt, cstring)
            return self.builder.call(
                callee_func, [format_ptr]+call_args[1:], 'calltmp'), None
        elif callee == 'malloc':
            return self.builder.call(
                callee_func, call_args[0:], 'calltmp'), None
        else:
            if callee_func is None or not isinstance(callee_func, ir.Function):
                raise CodegenError('Call to unknown function', node.callee)

            if node.args and len(callee_func.args) != len(node.args.exprs):
                raise CodegenError('Call argument length mismatch', node.callee)
            return self.builder.call(callee_func, call_args, 'calltmp'), None

    def codegen_Decl(self, node):
        if isinstance(node.type, c_ast.TypeDecl):
            type_str = node.type.type.names[0]
            # import pdb;pdb.set_trace()
            if type_str == "int":
                ir_type = ir.IntType(64)
                init = 0
            else:
                ir_type = ir.DoubleType()
                init = 0.0
            # import pdb;pdb.set_trace()
            if node.init is not None:
                init_val, _ = self.codegen(node.init)
            else:
                init_val = ir.values.Constant(ir_type, init)

            var_addr, var_ir_type = self.create_entry_block_alloca(
                node.name, type_str, 1)

            if isinstance(init_val.type, ir.IntType) and \
                    isinstance(ir_type, ir.DoubleType):
                if self.builder:
                    init_val = self.builder.uitofp(init_val, ir.DoubleType(), 'booltmp')

            if self.in_global:
                var_addr.initializer = init_val
            else:
                self.builder.store(init_val, var_addr)

        elif isinstance(node.type, c_ast.ArrayDecl):
            # At now only support Int
            array_list = []
            array_node = node.type

            while True:
                array_next_type = array_node.type
                if isinstance(array_next_type, c_ast.TypeDecl):
                    array_list.append(int(array_node.dim.value))
                    type_str = array_next_type.type.names[0]
                    break

                elif isinstance(array_next_type, c_ast.ArrayDecl):
                    array_list.append(int(array_node.dim.value))
                    array_node = array_next_type
                    continue
                pass

            var_addr, var_ir_type = self.create_entry_block_alloca(
                node.name, type_str, 1, array_list)


        elif isinstance(node.type, c_ast.PtrDecl):

            point_level = 1
            # the type is recursive.
            sub_node = node.type

            while True:
                sub_next_type = sub_node.type
                if isinstance(sub_next_type, c_ast.TypeDecl):
                    # pointer_list.append(int(sub_node.dim.value))
                    #
                    type_str = sub_next_type.type.names[0]
                    break
                elif isinstance(sub_next_type, c_ast.PtrDecl):
                    # At now I only care about **P not *a[4]
                    # make the easy work done first
                    point_level += 1
                    sub_node = sub_next_type
                    continue
                pass

            var_addr, var_ir_type= self.create_entry_block_alloca(
                node.name, type_str, 1, point_level=point_level)
        else:
            return None, None

        return None, var_addr

    def codegen_ID(self, node):
        node.show()
        valtype, var = self.lookup(node.name)
        node.ir_type = valtype
        return self.builder.load(var), var

    def codegen_ArrayRef(self, node):
        node.show()
        name = node.name
        subscript = node.subscript
        name_ir, name_ptr = self.codegen(name)
        value_ir_type = name.ir_type.element
        subscript_ir, subscript_ptr = self.codegen(subscript)
        if len(name.ir_type.dim_array) > 1:
            level_lenth = name.ir_type.dim_array[-1] * 8
        else:
            level_lenth = 1 * 8

        dim_lenth = ir.Constant(ir.IntType(64), level_lenth)
        subscript_ir = self.builder.fptoui(subscript_ir, ir.IntType(64))
        subscript_value_in_array = self.builder.mul(
            dim_lenth, subscript_ir, "array_add")
        name_ptr_int = self.builder.ptrtoint(name_ptr, ir.IntType(64))
        value_ptr = self.builder.add(
            subscript_value_in_array, name_ptr_int, 'addtmp')
        # import pdb;pdb.set_trace()
        value_ptr = self.builder.inttoptr(
            value_ptr, ir.PointerType(value_ir_type))
        value_result = self.builder.load(value_ptr)

        # the node.ir_type should be used in somewhere
        node.ir_type = name.ir_type.gep(ir.Constant(ir.IntType(64), 0))
        node.ir_type.dim_array = name.ir_type.dim_array[:-1]
        return value_result, value_ptr

    def codegen_stmt(self, node):
        typ = type(node)
        if typ in (
                c_ast.Decl, c_ast.Assignment, c_ast.Cast, c_ast.UnaryOp,
                c_ast.BinaryOp, c_ast.TernaryOp, c_ast.FuncCall, c_ast.ArrayRef,
                c_ast.StructRef):

            return self.codegen(node)[0], None

        elif typ in (c_ast.Compound,):
            # No extra indentation required before the opening brace of a
            # compound - because it consists of multiple lines it has to
            # compute its own indentation.
            return self.codegen(node)[0], None

    def codegen_Return(self, node):
        node.show()
        retval, _ = self.codegen(node.expr)
        self.builder.ret(retval), None

    def codegen_Compound(self, node):
        node.show()

        if node.block_items:
            for stmt in node.block_items:
                self.codegen(stmt)
        return None, None

    def codegen_FuncDecl(self, node):

        node.show()
        if isinstance(node.type, c_ast.PtrDecl):
            # only support one level ptr just for simple use
            return_type_str = node.type.type.type.names[0]
            if return_type_str == "int":
                data_ir_type = ir.IntType(64)
            else:
                data_ir_type = ir.DoubleType()
            ir_type = ir.PointerType(data_ir_type)

        else:
            return_type_str = node.type.type.names[0]
            if return_type_str == "int":
                ir_type = ir.IntType(64)
            else:
                ir_type = ir.DoubleType()

        return ir_type, None


    def codegen_FuncDef(self, node):
        node.show()

        # import pdb;pdb.set_trace()
        # deep level func have deep level
        self.in_builder = False

        # we don't want funcdecl in codegen_decl too
        ir_type, _ = self.codegen(node.decl.type)
        funcname = node.decl.name

        if funcname == "main":
            self.return_type = ir_type  # for call in C

        arg_types = []
        if node.decl.type.args:
            for arg_type in node.decl.type.args.params:
                arg_types.append(get_ir_type_from_node(arg_type))

        with self.new_function():

            self.function = ir.Function(
                self.module,
                ir.FunctionType(ir_type, arg_types),
                name=funcname)
            self.block = self.function.append_basic_block()
            self.builder = ir.IRBuilder(self.block)
            self.define(funcname, (ir_type, self.function))
            if node.decl.type.args:
                for i, p in enumerate(node.decl.type.args.params):
                    arg_type = arg_types[i]
                    # breakpoint()
                    var = self.builder.alloca(arg_type, name=p.name)
                    self.define(p.name, (arg_type, var))
                    self.builder.store(self.function.args[i], var)

            self.codegen(node.body)

            if not self.builder.block.is_terminated:
                self.builder.ret(ir.Constant(ir.IntType(64), int(0)))

            return None, None


    def codegen_FunctionAST(self, node):

        self.func_symtab = {}
        func, _ = self.codegen(node.proto)
        bb_entry = func.append_basic_block('entry')
        self.builder = ir.IRBuilder(bb_entry)

        # Add all arguments to the symbol table and create their allocas
        for i, arg in enumerate(func.args):
            arg.name = node.proto.argnames[i]
            alloca = self.builder.alloca(ir.DoubleType(), name=arg.name)
            self.builder.store(arg, alloca)
            self.func_symtab[arg.name] = alloca

        retval, _ = self.codegen(node.body)
        self.builder.ret(retval)
        return func, None
