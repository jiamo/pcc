import llvmlite.ir as ir
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


class CodegenError(Exception):
    pass


def get_ir_type(type_str):
    # only support int and double
    if type_str == "int":
        ir_type = ir.IntType(64)
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
        self.builder = None
        self.func_symtab = {}
        self.func_tyinfo = {}
        self.global_symtab = {}
        self.global_tyinfo = {}
        self.global_builder = ir.IRBuilder()
        self.in_builder = None

    def generate_code(self, node):
        return self.codegen(node)

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

        if self.builder:
            ret = self.builder.alloca(ir_type, size=None, name=name)
            self.func_symtab[name] = ret
            self.func_tyinfo[name] = ir_type
        else:
            # it is global ?
            ret = ir.GlobalVariable(self.module, ir_type, name)
            # ret = self.global_builder.alloca(ir_type, size=None, name=name)
            self.global_symtab[name] = ret
            self.global_tyinfo[name] = ir_type

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
            lv_addr = self.func_symtab[node.expr.name]  # TODO fix it
            lv = self.builder.load(lv_addr, node.expr.name)
            rv = ir.Constant(ir.IntType(64), 1)
            addresult = self.builder.add(lv, rv, 'addtmp')
            result = self.builder.store(addresult, lv_addr), None

        if node.op == "p--":
            lv_addr = self.func_symtab[node.expr.name]  # TODO fix it
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
        elif node.op in [">", "<", ">=", "<=", "!="]:
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
        else_bb = ir.Block(self.builder.function, 'else')
        merge_bb = ir.Block(self.builder.function, 'ifend')

        self.builder.cbranch(cmp, then_bb, else_bb)
        self.builder.position_at_start(then_bb)
        if_return = False
        else_return = False
        then_val, _ = self.codegen(node.iftrue)
        # if return at it we don't branch
        if not self.return_in_branch:
            self.builder.branch(merge_bb)
        else:
            self.return_in_branch.pop()
            if_return = True
        then_bb = self.builder.block
        # Emit the 'else' part
        self.builder.function.basic_blocks.append(else_bb)
        self.builder.position_at_start(else_bb)
        if node.iffalse:
            else_val, _ = self.codegen(node.iffalse)
            else_bb = self.builder.block

        if not self.return_in_branch:
            self.builder.branch(merge_bb)
        else:
            self.return_in_branch.pop()
            else_return = True

        # Emit the merge ('ifend') block
        if if_return and else_return:
            pass
        else:
            self.builder.function.basic_blocks.append(merge_bb)
        self.builder.position_at_start(merge_bb)
        return None, None

    def codegen_NoneType(self, node):
        return None, None

    def codegen_For(self, node):

        saved_block = self.builder.block
        id_name = node.__class__.__name__
        self.builder.position_at_end(
            saved_block)  # why the save_block at the end

        start_val, _ = self.codegen(node.init)

        # The builder is what? loop is a block which begin with loop
        test_bb = self.builder.function.append_basic_block('test')
        loop_bb = self.builder.function.append_basic_block('loop')
        after_bb = self.builder.function.append_basic_block('afterloop')

        self.builder.branch(test_bb)
        self.builder.position_at_start(test_bb)

        endcond, _ = self.codegen(node.cond)
        cmp = self.builder.fcmp_ordered(
            '!=', endcond, ir.values.Constant(ir.DoubleType(), 0.0),
            'loopcond')

        self.builder.cbranch(cmp, loop_bb, after_bb)

        self.builder.position_at_start(loop_bb)
        body_val, _ = self.codegen(node.stmt)

        self.codegen(node.next)
        self.builder.branch(test_bb)
        self.builder.position_at_start(after_bb)
        return ir.values.Constant(ir.DoubleType(), 0.0), None

    def codegen_While(self, node):

        node.show()
        saved_block = self.builder.block
        id_name = node.__class__.__name__
        self.builder.position_at_end(saved_block)
        # The builder is what? loop is a block which begin with loop
        test_bb = self.builder.function.append_basic_block('test')
        loop_bb = self.builder.function.append_basic_block('loop')
        after_bb = self.builder.function.append_basic_block('afterloop')

        self.builder.branch(test_bb)
        self.builder.position_at_start(test_bb)

        endcond, _ = self.codegen(node.cond)
        cmp = self.builder.fcmp_ordered(
            '!=', endcond, ir.values.Constant(ir.DoubleType(), 0.0),
            'loopcond')

        self.builder.cbranch(cmp, loop_bb, after_bb)

        self.builder.position_at_start(loop_bb)
        body_val, _ = self.codegen(node.stmt)

        # New code will be inserted into after_bb
        self.builder.branch(test_bb)
        self.builder.position_at_start(after_bb)

        # The 'for' expression always returns 0
        return ir.values.Constant(ir.DoubleType(), 0.0)

    def codegen_FuncCall(self, node):
        node.show()
        callee = None

        if isinstance(node.name, c_ast.ID):
            callee = node.name.name

        callee_func = self.module.globals.get(callee, None)
        call_args = [self.codegen(arg)[0] for arg in node.args.exprs]

        # just for see and hard code it
        if callee == "printf":
            cstring = voidptr_t
            fnty = ir.FunctionType(int32_t, [cstring], var_arg=True)
            callee_func = ir.Function(self.module, fnty, name="printf")
            print("callee_func", callee_func)
            # import pdb;pdb.set_trace()
            # cast array to pointer
            data_fmt = call_args[0]
            global_fmt = ir.GlobalVariable(
                self.module, data_fmt.type, "printf_format")
            # global_fmt.linkage = "internal"
            # global_fmt.global_constant = True
            global_fmt.initializer = data_fmt
            format_ptr = self.builder.bitcast(global_fmt, cstring)
            return self.builder.call(
                callee_func, [format_ptr] + call_args[1:], 'calltmp'), None
        elif callee == 'malloc':
            # TODO try to make it return (void *) not the (int *)
            fnty = ir.FunctionType(int64ptr_t, [int64_t], var_arg=True)
            callee_func = ir.Function(self.module, fnty, name="malloc")
            return self.builder.call(
                callee_func, call_args[0:], 'calltmp'), None
        else:
            if callee_func is None or not isinstance(callee_func, ir.Function):
                raise CodegenError('Call to unknown function', node.callee)

            if len(callee_func.args) != len(node.args.exprs):
                raise CodegenError('Call argument length mismatch', node.callee)
            return self.builder.call(callee_func, call_args, 'calltmp'), None

    def codegen_Decl(self, node):
        # may be it is just for for value decl how to detect it is global

        if self.in_builder:
            saved_block = self.builder.block
        else:
            saved_block = None
        # import pdb;pdb.set_trace()
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
            if self.in_builder:
                self.builder.position_at_end(saved_block)

            if isinstance(init_val.type, ir.IntType) and \
                    isinstance(ir_type, ir.DoubleType):
                if self.builder:
                    init_val = self.builder.uitofp(init_val, ir.DoubleType(), 'booltmp')

            if self.in_builder:
                self.builder.store(init_val, var_addr)
            else:
                self.global_symtab[node.name].initializer = init_val
                # no need wrap again
                # self.global_symtab[node.name].linkage = "internal"

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

            var_addr , var_ir_type= self.create_entry_block_alloca(
                node.name, type_str, 1, point_level=point_level)
        else:
            return None, None

        # self.func_symtab[node.name] = var_addr

        return None, var_addr

    def codegen_ID(self, node):
        node.show()
        # import pdb;pdb.set_trace()
        var_addr = self.func_symtab.get(node.name) or self.global_symtab[node.name]
        ret = self.builder.load(var_addr, node.name)
        node.ir_type = self.func_tyinfo.get(node.name) or self.global_tyinfo[node.name]
        return ret, var_addr

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
        # but if we have a return the builder? is set before
        # return self._set_terminator(
        #     instructions.Ret(self.block, "ret", value))
        self.return_in_branch.append(True)
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
                arg_types.append(
                    get_ir_type_from_node(arg_type))

        if node.decl.type.args is not None:
            func_ty = ir.FunctionType(
                ir_type,
                arg_types)
        else:
            func_ty = ir.FunctionType(ir_type, [])

        if funcname in self.module.globals:
            existing_func = self.module[funcname]
            if not isinstance(existing_func, ir.Function):
                raise CodegenError('Function/Global name collision', funcname)
            if not existing_func.is_declaration():
                raise CodegenError('Redifinition of {0}'.format(funcname))
            if len(existing_func.function_type.args) != len(func_ty.args):
                raise CodegenError(
                    'Redifinition with different number of arguments')
            func = self.module.globals[funcname]
        else:
            # Otherwise create a new function into the module
            # no record by this class but by ir
            func = ir.Function(self.module, func_ty, funcname)

        bb_entry = func.append_basic_block(funcname + "_entry")
        self.builder = ir.IRBuilder(bb_entry)
        self.return_in_branch = []
        self.func_symtab = {}
        self.func_tyinfo = {}
        self.in_builder = True

        for i, arg in enumerate(func.args):
            arg.name = node.decl.type.args.params[i].name
            # import pdb;pdb.set_trace()
            arg_type = arg.type
            alloca = self.builder.alloca(arg_type, name=arg.name)
            self.builder.store(arg, alloca)
            self.func_symtab[arg.name] = alloca
            self.func_tyinfo[arg.name] = arg_type

        self.codegen(node.body)
        return func, None

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
