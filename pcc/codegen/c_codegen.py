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
struct_types = {}

# Libc function signature registry: name -> (return_type, [param_types], var_arg)
# Covers: stdio.h, stdlib.h, string.h, ctype.h, math.h, unistd.h, time.h
_VOID = ir.VoidType()
_double = ir.DoubleType()
_FILE_ptr = voidptr_t  # FILE* modeled as opaque void*
_size_t = int64_t
_time_t = int64_t

LIBC_FUNCTIONS = {
    # === stdio.h ===
    "printf":    (int32_t,   [cstring],                              True),
    "fprintf":   (int32_t,   [_FILE_ptr, cstring],                   True),
    "sprintf":   (int32_t,   [cstring, cstring],                     True),
    "snprintf":  (int32_t,   [cstring, _size_t, cstring],            True),
    "vprintf":   (int32_t,   [cstring, voidptr_t],                   False),
    "vfprintf":  (int32_t,   [_FILE_ptr, cstring, voidptr_t],        False),
    "vsprintf":  (int32_t,   [cstring, cstring, voidptr_t],          False),
    "vsnprintf": (int32_t,   [cstring, _size_t, cstring, voidptr_t], False),
    "scanf":     (int32_t,   [cstring],                              True),
    "fscanf":    (int32_t,   [_FILE_ptr, cstring],                   True),
    "sscanf":    (int32_t,   [cstring, cstring],                     True),
    "fopen":     (_FILE_ptr, [cstring, cstring],                     False),
    "fclose":    (int32_t,   [_FILE_ptr],                            False),
    "fread":     (_size_t,   [voidptr_t, _size_t, _size_t, _FILE_ptr], False),
    "fwrite":    (_size_t,   [voidptr_t, _size_t, _size_t, _FILE_ptr], False),
    "fseek":     (int32_t,   [_FILE_ptr, int64_t, int32_t],          False),
    "ftell":     (int64_t,   [_FILE_ptr],                            False),
    "rewind":    (_VOID,     [_FILE_ptr],                            False),
    "feof":      (int32_t,   [_FILE_ptr],                            False),
    "ferror":    (int32_t,   [_FILE_ptr],                            False),
    "fflush":    (int32_t,   [_FILE_ptr],                            False),
    "fgets":     (cstring,   [cstring, int32_t, _FILE_ptr],          False),
    "fputs":     (int32_t,   [cstring, _FILE_ptr],                   False),
    "fgetc":     (int32_t,   [_FILE_ptr],                            False),
    "fputc":     (int32_t,   [int32_t, _FILE_ptr],                   False),
    "getc":      (int32_t,   [_FILE_ptr],                            False),
    "putc":      (int32_t,   [int32_t, _FILE_ptr],                   False),
    "getchar":   (int32_t,   [],                                     False),
    "putchar":   (int32_t,   [int32_t],                              False),
    "ungetc":    (int32_t,   [int32_t, _FILE_ptr],                   False),
    "puts":      (int32_t,   [cstring],                              False),
    "perror":    (_VOID,     [cstring],                              False),
    "remove":    (int32_t,   [cstring],                              False),
    "rename":    (int32_t,   [cstring, cstring],                     False),

    # === stdlib.h ===
    "malloc":    (voidptr_t, [_size_t],                              False),
    "calloc":    (voidptr_t, [_size_t, _size_t],                     False),
    "realloc":   (voidptr_t, [voidptr_t, _size_t],                   False),
    "free":      (_VOID,     [voidptr_t],                            False),
    "exit":      (_VOID,     [int32_t],                              False),
    "_Exit":     (_VOID,     [int32_t],                              False),
    "abort":     (_VOID,     [],                                     False),
    "atexit":    (int32_t,   [voidptr_t],                            False),
    "abs":       (int32_t,   [int32_t],                              False),
    "labs":      (int64_t,   [int64_t],                              False),
    "atoi":      (int32_t,   [cstring],                              False),
    "atol":      (int64_t,   [cstring],                              False),
    "atof":      (_double,   [cstring],                              False),
    "strtol":    (int64_t,   [cstring, voidptr_t, int32_t],          False),
    "strtoul":   (int64_t,   [cstring, voidptr_t, int32_t],          False),
    "strtod":    (_double,   [cstring, voidptr_t],                   False),
    "strtof":    (_double,   [cstring, voidptr_t],                   False),
    "rand":      (int32_t,   [],                                     False),
    "srand":     (_VOID,     [int32_t],                              False),
    "qsort":     (_VOID,     [voidptr_t, _size_t, _size_t, voidptr_t], False),
    "bsearch":   (voidptr_t, [voidptr_t, voidptr_t, _size_t, _size_t, voidptr_t], False),
    "getenv":    (cstring,   [cstring],                              False),
    "system":    (int32_t,   [cstring],                              False),

    # === string.h ===
    "strlen":    (_size_t,   [cstring],                              False),
    "strcmp":    (int32_t,   [cstring, cstring],                     False),
    "strncmp":   (int32_t,   [cstring, cstring, _size_t],            False),
    "strcpy":    (cstring,   [cstring, cstring],                     False),
    "strncpy":   (cstring,   [cstring, cstring, _size_t],            False),
    "strcat":    (cstring,   [cstring, cstring],                     False),
    "strncat":   (cstring,   [cstring, cstring, _size_t],            False),
    "strchr":    (cstring,   [cstring, int32_t],                     False),
    "strrchr":   (cstring,   [cstring, int32_t],                     False),
    "strstr":    (cstring,   [cstring, cstring],                     False),
    "strpbrk":   (cstring,   [cstring, cstring],                     False),
    "strspn":    (_size_t,   [cstring, cstring],                     False),
    "strcspn":   (_size_t,   [cstring, cstring],                     False),
    "strtok":    (cstring,   [cstring, cstring],                     False),
    "memset":    (voidptr_t, [voidptr_t, int32_t, _size_t],          False),
    "memcpy":    (voidptr_t, [voidptr_t, voidptr_t, _size_t],        False),
    "memmove":   (voidptr_t, [voidptr_t, voidptr_t, _size_t],        False),
    "memcmp":    (int32_t,   [voidptr_t, voidptr_t, _size_t],        False),
    "memchr":    (voidptr_t, [voidptr_t, int32_t, _size_t],          False),
    "strerror":  (cstring,   [int32_t],                              False),

    # === ctype.h ===
    "isalpha":   (int32_t,   [int32_t],                              False),
    "isdigit":   (int32_t,   [int32_t],                              False),
    "isalnum":   (int32_t,   [int32_t],                              False),
    "isspace":   (int32_t,   [int32_t],                              False),
    "isupper":   (int32_t,   [int32_t],                              False),
    "islower":   (int32_t,   [int32_t],                              False),
    "isprint":   (int32_t,   [int32_t],                              False),
    "ispunct":   (int32_t,   [int32_t],                              False),
    "iscntrl":   (int32_t,   [int32_t],                              False),
    "isxdigit":  (int32_t,   [int32_t],                              False),
    "isgraph":   (int32_t,   [int32_t],                              False),
    "toupper":   (int32_t,   [int32_t],                              False),
    "tolower":   (int32_t,   [int32_t],                              False),

    # === math.h ===
    "sin":       (_double,   [_double],                              False),
    "cos":       (_double,   [_double],                              False),
    "tan":       (_double,   [_double],                              False),
    "asin":      (_double,   [_double],                              False),
    "acos":      (_double,   [_double],                              False),
    "atan":      (_double,   [_double],                              False),
    "atan2":     (_double,   [_double, _double],                     False),
    "sinh":      (_double,   [_double],                              False),
    "cosh":      (_double,   [_double],                              False),
    "tanh":      (_double,   [_double],                              False),
    "exp":       (_double,   [_double],                              False),
    "exp2":      (_double,   [_double],                              False),
    "log":       (_double,   [_double],                              False),
    "log2":      (_double,   [_double],                              False),
    "log10":     (_double,   [_double],                              False),
    "pow":       (_double,   [_double, _double],                     False),
    "sqrt":      (_double,   [_double],                              False),
    "cbrt":      (_double,   [_double],                              False),
    "hypot":     (_double,   [_double, _double],                     False),
    "ceil":      (_double,   [_double],                              False),
    "floor":     (_double,   [_double],                              False),
    "round":     (_double,   [_double],                              False),
    "trunc":     (_double,   [_double],                              False),
    "fmod":      (_double,   [_double, _double],                     False),
    "fabs":      (_double,   [_double],                              False),
    "ldexp":     (_double,   [_double, int32_t],                     False),

    # === time.h ===
    "time":      (_time_t,   [voidptr_t],                            False),
    "clock":     (int64_t,   [],                                     False),
    "difftime":  (_double,   [_time_t, _time_t],                     False),

    # === unistd.h (POSIX) ===
    "sleep":     (int32_t,   [int32_t],                              False),
    "usleep":    (int32_t,   [int32_t],                              False),
    "read":      (int64_t,   [int32_t, voidptr_t, _size_t],          False),
    "write":     (int64_t,   [int32_t, voidptr_t, _size_t],          False),
    "open":      (int32_t,   [cstring, int32_t],                     True),
    "close":     (int32_t,   [int32_t],                              False),
    "getpid":    (int32_t,   [],                                     False),
    "getppid":   (int32_t,   [],                                     False),
}

class CodegenError(Exception):
    pass


def get_ir_type(type_str):
    if type_str == "int":
        return int64_t
    elif type_str == "char":
        return int8_t
    elif type_str == "void":
        return ir.VoidType()
    else:
        return ir.DoubleType()


def get_ir_type_from_node(node):

    if isinstance(node.type, c_ast.PtrDecl):
        return_type_str = node.type.type.type.names[0]
        data_ir_type = get_ir_type(return_type_str)
        ir_type = ir.PointerType(data_ir_type)
    else:
        return_type_str = node.type.type.names[0]
        ir_type = get_ir_type(return_type_str)
    return ir_type


class LLVMCodeGenerator(object):

    def __init__(self):
        self.module = ir.Module()

        #
        self.builder = None
        self.global_builder:IRBuilder = ir.IRBuilder()
        self.env = ChainMap()
        self.nlabels = 0
        self.function = None
        self.in_global = True
        self._declared_libc = set()

    def define(self, name, val):
        self.env[name] = val

    def lookup(self, name):
        if name not in self.env and name in LIBC_FUNCTIONS:
            self._declare_libc(name)
        return self.env[name]

    def _declare_libc(self, name):
        """Lazily declare a libc function on first use."""
        ret_type, param_types, var_arg = LIBC_FUNCTIONS[name]
        fnty = ir.FunctionType(ret_type, param_types, var_arg=var_arg)
        func = ir.Function(self.module, fnty, name=name)
        self.define(name, (fnty, func))
        self._declared_libc.add(name)

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

        elif type_str == "char":
            ir_type = ir.IntType(8)

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
            ret.initializer = ir.Constant(ir_type, None)  # zero-initialize
            self.define(name, (ir_type, ret))

        return ret, ir_type

    def codegen(self, node):
        method = 'codegen_' + node.__class__.__name__
        return getattr(self, method)(node)

    def codegen_FileAST(self, node):
        for ext in node.ext:
            self.codegen(ext)

    _escape_map = {
        'n': '\n', 't': '\t', 'r': '\r', '\\': '\\',
        '0': '\0', "'": "'", '"': '"', 'a': '\a',
        'b': '\b', 'f': '\f', 'v': '\v',
    }

    def _process_escapes(self, s):
        """Process C escape sequences in a string."""
        result = []
        i = 0
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s):
                esc = self._escape_map.get(s[i + 1])
                if esc is not None:
                    result.append(esc)
                    i += 2
                    continue
            result.append(s[i])
            i += 1
        return ''.join(result)

    def codegen_Constant(self, node):

        if node.type == "int":
            # Support hex (0xFF), octal (077), and decimal literals
            val_str = node.value.rstrip('uUlL')
            if val_str.startswith('0x') or val_str.startswith('0X'):
                int_val = int(val_str, 16)
            elif val_str.startswith('0') and len(val_str) > 1 and val_str[1:].isdigit():
                int_val = int(val_str, 8)
            else:
                int_val = int(val_str)
            return ir.values.Constant(ir.IntType(64), int_val), None
        elif node.type == 'char':
            # char constant like 'a' -> i8
            char_val = self._process_escapes(node.value[1:-1])
            return ir.values.Constant(int8_t, ord(char_val[0])), None
        elif node.type == 'string':
            raw = node.value[1:-1]
            processed = self._process_escapes(raw)
            b = bytearray(processed + '\00', encoding='ascii')
            n = len(b)
            array = ir.ArrayType(ir.IntType(8), n)
            tmp = ir.values.Constant(array, b)
            return tmp, None
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
            ("*=", dispatch_type_double): self.builder.fmul,
            ("*=", dispatch_type_int): self.builder.mul,
            ("/=", dispatch_type_double): self.builder.fdiv,
            ("/=", dispatch_type_int): self.builder.sdiv,
            ("%=", dispatch_type_int): self.builder.srem,
            ("%=", dispatch_type_double): self.builder.frem,
            ("<<=", dispatch_type_int): self.builder.shl,
            (">>=", dispatch_type_int): self.builder.ashr,
            ("&=", dispatch_type_int): self.builder.and_,
            ("|=", dispatch_type_int): self.builder.or_,
            ("^=", dispatch_type_int): self.builder.xor,
        }
        if isinstance(lv.type, ir.IntType) and isinstance(rv.type, ir.IntType):
            dispatch_type = dispatch_type_int
        else:
            dispatch_type = dispatch_type_double
        dispatch = (node.op, dispatch_type)
        handle = dispatch_dict.get(dispatch)

        if node.op == '=':
            # Type coercion for store
            if rv.type != lv.type:
                if isinstance(rv.type, ir.PointerType) and isinstance(lv.type, ir.PointerType):
                    rv = self.builder.bitcast(rv, lv.type)
                else:
                    rv = self._implicit_convert(rv, lv.type)
            self.builder.store(rv, lv_addr)
            return rv, lv_addr  # return value for chained assignment
        else:
            addresult = handle(lv, rv, 'addtmp')
            self.builder.store(addresult, lv_addr)
            return addresult, lv_addr

    def codegen_UnaryOp(self, node):

        result = None
        result_ptr = None

        if node.op == "p++":
            # post-increment: return old value
            _, lv_addr = self.lookup(node.expr.name)
            lv = self.builder.load(lv_addr, node.expr.name)
            if isinstance(lv.type, ir.PointerType):
                addresult = self.builder.gep(lv, [ir.Constant(int64_t, 1)], name='ptrinc')
            else:
                addresult = self.builder.add(lv, ir.Constant(lv.type, 1), 'addtmp')
            self.builder.store(addresult, lv_addr)
            result = lv

        elif node.op == "p--":
            _, lv_addr = self.lookup(node.expr.name)
            lv = self.builder.load(lv_addr, node.expr.name)
            if isinstance(lv.type, ir.PointerType):
                addresult = self.builder.gep(lv, [ir.Constant(int64_t, -1)], name='ptrdec')
            else:
                addresult = self.builder.sub(lv, ir.Constant(lv.type, 1), 'subtmp')
            self.builder.store(addresult, lv_addr)
            result = lv

        elif node.op == "++":
            _, lv_addr = self.lookup(node.expr.name)
            lv = self.builder.load(lv_addr, node.expr.name)
            if isinstance(lv.type, ir.PointerType):
                addresult = self.builder.gep(lv, [ir.Constant(int64_t, 1)], name='ptrinc')
            else:
                addresult = self.builder.add(lv, ir.Constant(lv.type, 1), 'addtmp')
            self.builder.store(addresult, lv_addr)
            result = addresult

        elif node.op == "--":
            _, lv_addr = self.lookup(node.expr.name)
            lv = self.builder.load(lv_addr, node.expr.name)
            if isinstance(lv.type, ir.PointerType):
                addresult = self.builder.gep(lv, [ir.Constant(int64_t, -1)], name='ptrdec')
            else:
                addresult = self.builder.sub(lv, ir.Constant(lv.type, 1), 'subtmp')
            self.builder.store(addresult, lv_addr)
            result = addresult

        elif node.op == '*':
            name_ir, name_ptr = self.codegen(node.expr)
            result_ptr = name_ir
            result = self.builder.load(result_ptr)

        elif node.op == '&':
            name_ir, name_ptr = self.codegen(node.expr)
            result_ptr = name_ptr
            result = result_ptr

        elif node.op == '+':
            operand, _ = self.codegen(node.expr)
            result = operand  # unary plus is a no-op

        elif node.op == '-':
            operand, _ = self.codegen(node.expr)
            if isinstance(operand.type, ir.IntType):
                result = self.builder.neg(operand, 'negtmp')
            else:
                result = self.builder.fneg(operand, 'negtmp')

        elif node.op == '!':
            operand, _ = self.codegen(node.expr)
            if isinstance(operand.type, ir.PointerType):
                null = ir.Constant(operand.type, None)
                cmp = self.builder.icmp_unsigned('==', operand, null, 'nottmp')
                result = self.builder.zext(cmp, int64_t, 'notres')
            elif isinstance(operand.type, ir.IntType):
                cmp = self.builder.icmp_signed('==', operand, ir.Constant(operand.type, 0), 'nottmp')
                result = self.builder.zext(cmp, int64_t, 'notres')
            else:
                cmp = self.builder.fcmp_ordered('==', operand, ir.Constant(ir.DoubleType(), 0.0), 'nottmp')
                result = self.builder.zext(cmp, int64_t, 'notres')

        elif node.op == '~':
            operand, _ = self.codegen(node.expr)
            result = self.builder.not_(operand, 'invtmp')

        elif node.op == 'sizeof':
            result = self._codegen_sizeof(node.expr)

        return result, result_ptr

    def _codegen_sizeof(self, expr):
        """Return sizeof as an i64 constant."""
        type_map = {"int": 8, "double": 8, "char": 1}  # our int is i64
        if isinstance(expr, c_ast.Typename):
            type_name = expr.type.type.names[0]
            size = type_map.get(type_name, 8)
        elif isinstance(expr, c_ast.ID):
            ir_type, _ = self.lookup(expr.name)
            size = self._ir_type_size(ir_type)
        else:
            val, _ = self.codegen(expr)
            size = self._ir_type_size(val.type)
        return ir.Constant(int64_t, size)

    def _resolve_type_str(self, type_str):
        """Resolve typedef'd type names to their base type string."""
        key = f'__typedef_{type_str}'
        if key in self.env:
            resolved = self.env[key]
            if isinstance(resolved, str):
                return resolved
            # It's an IR type (e.g., struct typedef)
            return resolved
        return type_str

    def _get_ir_type(self, type_str):
        """Get IR type, resolving typedefs."""
        resolved = self._resolve_type_str(type_str)
        if isinstance(resolved, ir.Type):
            return resolved
        return get_ir_type(resolved)

    def _build_const_array_init(self, init_list, array_type, elem_ir_type):
        """Build a constant initializer for a global array."""
        values = []
        for expr in init_list.exprs:
            if isinstance(expr, c_ast.InitList):
                sub_type = array_type.element if isinstance(array_type, ir.ArrayType) else array_type
                values.append(self._build_const_array_init(expr, sub_type, elem_ir_type))
            else:
                val = self._eval_const_expr(expr)
                values.append(ir.Constant(elem_ir_type, val))
        return ir.Constant(array_type, values)

    def _init_array(self, base_addr, init_list, elem_ir_type, prefix_idx):
        """Recursively initialize array elements from an InitList."""
        for i, expr in enumerate(init_list.exprs):
            idx = prefix_idx + [ir.Constant(ir.IntType(32), i)]
            if isinstance(expr, c_ast.InitList):
                self._init_array(base_addr, expr, elem_ir_type, idx)
            else:
                val, _ = self.codegen(expr)
                val = self._implicit_convert(val, elem_ir_type)
                elem_ptr = self.builder.gep(base_addr, idx, inbounds=True)
                self.builder.store(val, elem_ptr)

    def _implicit_convert(self, val, target_type):
        """Convert val to target_type if needed (implicit C promotion/truncation)."""
        if val.type == target_type:
            return val
        # int -> double
        if isinstance(val.type, ir.IntType) and isinstance(target_type, ir.DoubleType):
            return self.builder.sitofp(val, target_type)
        # double -> int
        if isinstance(val.type, ir.DoubleType) and isinstance(target_type, ir.IntType):
            return self.builder.fptosi(val, target_type)
        # int -> int (wider or narrower)
        if isinstance(val.type, ir.IntType) and isinstance(target_type, ir.IntType):
            if val.type.width < target_type.width:
                return self.builder.sext(val, target_type)
            elif val.type.width > target_type.width:
                return self.builder.trunc(val, target_type)
        return val

    def _to_bool(self, val, name='cond'):
        """Convert any value to an i1 boolean (!=0)."""
        if isinstance(val.type, ir.IntType):
            if val.type.width == 1:
                return val
            return self.builder.icmp_signed('!=', val, ir.Constant(val.type, 0), name)
        elif isinstance(val.type, ir.PointerType):
            null = ir.Constant(val.type, None)
            return self.builder.icmp_unsigned('!=', val, null, name)
        else:
            return self.builder.fcmp_ordered('!=', val, ir.Constant(ir.DoubleType(), 0.0), name)

    def _ir_type_size(self, ir_type):
        """Estimate byte size of an IR type."""
        if isinstance(ir_type, ir.IntType):
            return ir_type.width // 8
        elif isinstance(ir_type, ir.DoubleType):
            return 8
        elif isinstance(ir_type, ir.PointerType):
            return 8
        elif isinstance(ir_type, ir.ArrayType):
            return int(ir_type.count) * self._ir_type_size(ir_type.element)
        elif isinstance(ir_type, ir.LiteralStructType):
            return sum(self._ir_type_size(e) for e in ir_type.elements)
        return 8

    def codegen_Typename(self, node):
        # Used inside sizeof(type) — not directly code-generated
        return None, None

    def codegen_BinaryOp(self, node):
        # Short-circuit && and || before evaluating both sides
        if node.op == '&&':
            return self._codegen_short_circuit_and(node)
        elif node.op == '||':
            return self._codegen_short_circuit_or(node)

        lhs, _ = self.codegen(node.left)
        rhs, _ = self.codegen(node.right)

        # Pointer arithmetic: ptr + int or ptr - int
        if node.op in ('+', '-') and isinstance(lhs.type, ir.PointerType) and isinstance(rhs.type, ir.IntType):
            if node.op == '-':
                rhs = self.builder.neg(rhs, 'negidx')
            return self.builder.gep(lhs, [rhs], name='ptradd'), None
        if node.op == '+' and isinstance(rhs.type, ir.PointerType) and isinstance(lhs.type, ir.IntType):
            return self.builder.gep(rhs, [lhs], name='ptradd'), None

        # Pointer subtraction: ptr - ptr -> int (element count)
        if node.op == '-' and isinstance(lhs.type, ir.PointerType) and isinstance(rhs.type, ir.PointerType):
            lhs_int = self.builder.ptrtoint(lhs, int64_t)
            rhs_int = self.builder.ptrtoint(rhs, int64_t)
            diff = self.builder.sub(lhs_int, rhs_int, 'ptrdiff')
            elem_size = self._ir_type_size(lhs.type.pointee)
            return self.builder.sdiv(diff, ir.Constant(int64_t, elem_size), 'ptrdiff_elems'), None

        # Promote mixed int/double -> double
        if isinstance(lhs.type, ir.IntType) and isinstance(rhs.type, ir.DoubleType):
            lhs = self.builder.sitofp(lhs, ir.DoubleType())
        elif isinstance(lhs.type, ir.DoubleType) and isinstance(rhs.type, ir.IntType):
            rhs = self.builder.sitofp(rhs, ir.DoubleType())
        # Promote mismatched int widths (e.g., i8 + i64)
        elif isinstance(lhs.type, ir.IntType) and isinstance(rhs.type, ir.IntType):
            if lhs.type.width != rhs.type.width:
                target = lhs.type if lhs.type.width > rhs.type.width else rhs.type
                lhs = self._implicit_convert(lhs, target)
                rhs = self._implicit_convert(rhs, target)

        dispatch_type_double = 1
        dispatch_type_int = 0
        dispatch_dict = {
            ("+", dispatch_type_double): self.builder.fadd,
            ("+", dispatch_type_int): self.builder.add,
            ("-", dispatch_type_double): self.builder.fsub,
            ("-", dispatch_type_int): self.builder.sub,
            ("*", dispatch_type_double): self.builder.fmul,
            ("*", dispatch_type_int): self.builder.mul,
            ("/", dispatch_type_double): self.builder.fdiv,
            ("/", dispatch_type_int): self.builder.sdiv,
            ("%", dispatch_type_double): self.builder.frem,
            ("%", dispatch_type_int): self.builder.srem,
        }
        if isinstance(lhs.type, ir.IntType) and isinstance(rhs.type,
                                                           ir.IntType):
            dispatch_type = dispatch_type_int
        else:
            dispatch_type = dispatch_type_double
        dispatch = (node.op, dispatch_type)
        handle = dispatch_dict.get(dispatch)

        if node.op in ['+', '-', '*', '/', '%']:
            return handle(lhs, rhs, 'tmp'), None
        elif node.op in [">", "<", ">=", "<=", "!=", "=="]:
            if isinstance(lhs.type, ir.PointerType) and isinstance(rhs.type, ir.PointerType):
                # Pointer comparison: convert to int first
                lhs_i = self.builder.ptrtoint(lhs, int64_t)
                rhs_i = self.builder.ptrtoint(rhs, int64_t)
                cmp = self.builder.icmp_unsigned(node.op, lhs_i, rhs_i, 'ptrcmp')
            elif dispatch_type == dispatch_type_int:
                cmp = self.builder.icmp_signed(node.op, lhs, rhs, 'cmptmp')
            else:
                cmp = self.builder.fcmp_unordered(node.op, lhs, rhs, 'cmptmp')
            return self.builder.zext(cmp, int64_t, 'booltmp'), None
        elif node.op == '&':
            return self.builder.and_(lhs, rhs, 'andtmp'), None
        elif node.op == '|':
            return self.builder.or_(lhs, rhs, 'ortmp'), None
        elif node.op == '^':
            return self.builder.xor(lhs, rhs, 'xortmp'), None
        elif node.op == '<<':
            return self.builder.shl(lhs, rhs, 'shltmp'), None
        elif node.op == '>>':
            return self.builder.ashr(lhs, rhs, 'shrtmp'), None
        else:
            func = self.module.globals.get('binary{0}'.format(node.op))
            return self.builder.call(func, [lhs, rhs], 'binop'), None

    def _codegen_short_circuit_and(self, node):
        """Short-circuit &&: if lhs is false, skip rhs."""
        lhs, _ = self.codegen(node.left)
        lhs_bool = self._to_bool(lhs, 'and_lhs')

        rhs_bb = self.builder.function.append_basic_block('and_rhs')
        merge_bb = self.builder.function.append_basic_block('and_merge')
        lhs_bb = self.builder.block

        self.builder.cbranch(lhs_bool, rhs_bb, merge_bb)

        self.builder.position_at_end(rhs_bb)
        rhs, _ = self.codegen(node.right)
        rhs_bool = self._to_bool(rhs, 'and_rhs')
        rhs_result = self.builder.zext(rhs_bool, int64_t, 'and_rhs_ext')
        rhs_bb_end = self.builder.block
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(int64_t, 'and_result')
        phi.add_incoming(ir.Constant(int64_t, 0), lhs_bb)
        phi.add_incoming(rhs_result, rhs_bb_end)
        return phi, None

    def _codegen_short_circuit_or(self, node):
        """Short-circuit ||: if lhs is true, skip rhs."""
        lhs, _ = self.codegen(node.left)
        lhs_bool = self._to_bool(lhs, 'or_lhs')

        rhs_bb = self.builder.function.append_basic_block('or_rhs')
        merge_bb = self.builder.function.append_basic_block('or_merge')
        lhs_bb = self.builder.block

        self.builder.cbranch(lhs_bool, merge_bb, rhs_bb)

        self.builder.position_at_end(rhs_bb)
        rhs, _ = self.codegen(node.right)
        rhs_bool = self._to_bool(rhs, 'or_rhs')
        rhs_result = self.builder.zext(rhs_bool, int64_t, 'or_rhs_ext')
        rhs_bb_end = self.builder.block
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(int64_t, 'or_result')
        phi.add_incoming(ir.Constant(int64_t, 1), lhs_bb)
        phi.add_incoming(rhs_result, rhs_bb_end)
        return phi, None

    def codegen_If(self, node):


        cond_val, _ = self.codegen(node.cond)
        cmp = self._to_bool(cond_val)

        then_bb = self.builder.function.append_basic_block('then')
        else_bb = self.builder.function.append_basic_block('else')
        merge_bb = self.builder.function.append_basic_block('ifend')

        self.builder.cbranch(cmp, then_bb, else_bb)

        with self.new_scope():
            self.builder.position_at_end(then_bb)
            self.codegen(node.iftrue)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        with self.new_scope():
            self.builder.position_at_end(else_bb)
            if node.iffalse:
                self.codegen(node.iffalse)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)
        self.builder.position_at_end(merge_bb)
        # self.builder.block = merge_bb

        return None, None

    def codegen_NoneType(self, node):
        return None, None

    def codegen_For(self, node):

        saved_block = self.builder.block
        self.builder.position_at_end(
            saved_block)  # why the save_block at the end

        if node.init is not None:
            self.codegen(node.init)

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

        if node.cond is not None:
            endcond, _ = self.codegen(node.cond)
            cmp = self._to_bool(endcond, 'loopcond')
            self.builder.cbranch(cmp, loop_bb, after_bb)
        else:
            # for(;;) - infinite loop, always branch to body
            self.builder.branch(loop_bb)

        with self.new_scope():
            self.define('break', after_bb)
            self.define('continue', next_bb)
            self.builder.position_at_end(loop_bb)
            body_val, _ = self.codegen(node.stmt)  # if was ready codegen
            if not self.builder.block.is_terminated:
                self.builder.branch(next_bb)
            self.builder.position_at_end(next_bb)
            if node.next is not None:
                self.codegen(node.next)
            self.builder.branch(test_bb)
        # this append_basic_blook change the label
        # after_bb = self.builder.function.append_basic_block(after_loop_label)
        self.builder.function.basic_blocks.append(after_bb)
        self.builder.position_at_end(after_bb)

        return ir.values.Constant(ir.DoubleType(), 0.0), None

    def codegen_While(self, node):


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
        cmp = self._to_bool(endcond, 'loopcond')
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
        return None, None

    def codegen_Continue(self, node):
        self.builder.branch(self.lookup('continue'))
        return None, None

    def codegen_DoWhile(self, node):

        saved_block = self.builder.block
        self.builder.position_at_end(saved_block)

        loop_bb = self.builder.function.append_basic_block('dowhile_body')
        test_bb = self.builder.function.append_basic_block('dowhile_test')
        after_bb = self.builder.function.append_basic_block('dowhile_end')

        self.builder.branch(loop_bb)

        with self.new_scope():
            self.define('break', after_bb)
            self.define('continue', test_bb)
            self.builder.position_at_end(loop_bb)
            self.codegen(node.stmt)
            if not self.builder.block.is_terminated:
                self.builder.branch(test_bb)

        self.builder.position_at_end(test_bb)
        endcond, _ = self.codegen(node.cond)
        cmp = self._to_bool(endcond, 'loopcond')
        self.builder.cbranch(cmp, loop_bb, after_bb)

        self.builder.position_at_end(after_bb)
        return ir.values.Constant(ir.DoubleType(), 0.0), None

    def codegen_Switch(self, node):

        cond_val, _ = self.codegen(node.cond)

        after_bb = self.builder.function.append_basic_block('switch_end')

        # Collect cases from the Compound body
        cases = []
        default_case = None
        if node.stmt and node.stmt.block_items:
            for item in node.stmt.block_items:
                if isinstance(item, c_ast.Case):
                    cases.append(item)
                elif isinstance(item, c_ast.Default):
                    default_case = item

        default_bb = self.builder.function.append_basic_block('switch_default') if default_case else after_bb

        switch_inst = self.builder.switch(cond_val, default_bb)

        with self.new_scope():
            self.define('break', after_bb)

            for case in cases:
                case_val, _ = self.codegen(case.expr)
                case_bb = self.builder.function.append_basic_block('switch_case')
                switch_inst.add_case(case_val, case_bb)
                self.builder.position_at_end(case_bb)
                for stmt in (case.stmts or []):
                    self.codegen(stmt)
                if not self.builder.block.is_terminated:
                    self.builder.branch(after_bb)

            if default_case:
                self.builder.position_at_end(default_bb)
                for stmt in (default_case.stmts or []):
                    self.codegen(stmt)
                if not self.builder.block.is_terminated:
                    self.builder.branch(after_bb)

        self.builder.position_at_end(after_bb)
        return None, None

    def codegen_TernaryOp(self, node):

        cond_val, _ = self.codegen(node.cond)
        cmp = self._to_bool(cond_val)

        then_bb = self.builder.function.append_basic_block('ternary_true')
        else_bb = self.builder.function.append_basic_block('ternary_false')
        merge_bb = self.builder.function.append_basic_block('ternary_end')

        self.builder.cbranch(cmp, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        true_val, _ = self.codegen(node.iftrue)
        true_bb_end = self.builder.block
        self.builder.branch(merge_bb)

        self.builder.position_at_end(else_bb)
        false_val, _ = self.codegen(node.iffalse)
        false_bb_end = self.builder.block
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(true_val.type, 'ternary')
        phi.add_incoming(true_val, true_bb_end)
        phi.add_incoming(false_val, false_bb_end)
        return phi, None

    def codegen_Cast(self, node):

        expr, ptr = self.codegen(node.expr)

        # Determine destination IR type
        to_type_node = node.to_type.type
        if isinstance(to_type_node, c_ast.PtrDecl):
            # Cast to pointer type: (char*)expr, (int*)expr
            inner_type_str = to_type_node.type.type.names[0]
            dest_type = ir.PointerType(get_ir_type(inner_type_str))
            return self.builder.bitcast(expr, dest_type), None

        dest_type_str = to_type_node.type.names[0]
        match (type(expr.type), dest_type_str):
            case (ir.types.DoubleType, "int"):
                return self.builder.fptosi(expr, int64_t), None
            case (ir.types.IntType, "double"):
                return self.builder.sitofp(expr, ir.DoubleType()), None
            case (ir.types.IntType, "char"):
                return self.builder.trunc(expr, int8_t), None
            case _:
                return expr, ptr


    def codegen_FuncCall(self, node):

        callee = None
        if isinstance(node.name, c_ast.ID):
            callee = node.name.name

        _, callee_func = self.lookup(callee)

        call_args = []
        if node.args:
            call_args = [self.codegen(arg)[0] for arg in node.args.exprs]

        if callee_func is None or not isinstance(callee_func, ir.Function):
            raise CodegenError('Call to unknown function', callee)

        # Convert arguments to match function parameter types
        converted = self._convert_call_args(call_args, callee_func)

        # Call and handle return type
        is_void = isinstance(callee_func.return_value.type, ir.VoidType)
        if is_void:
            self.builder.call(callee_func, converted)
            return ir.Constant(int64_t, 0), None

        result = self.builder.call(callee_func, converted, 'calltmp')

        # Widen small int returns (e.g., i32 from strcmp) to i64
        if isinstance(result.type, ir.IntType) and result.type.width < 64:
            result = self.builder.sext(result, int64_t, 'retext')

        return result, None

    def _convert_call_args(self, call_args, callee_func):
        """Convert call arguments to match function parameter types."""
        converted = []
        param_types = [p.type for p in callee_func.args]

        for i, arg in enumerate(call_args):
            if i < len(param_types):
                expected = param_types[i]
                arg = self._coerce_arg(arg, expected)
            # var_arg: extra args beyond declared params pass through
            converted.append(arg)
        return converted

    def _coerce_arg(self, arg, expected):
        """Coerce a single argument to the expected type."""
        if arg.type == expected:
            return arg
        # String literal [N x i8] -> pointer
        if isinstance(arg.type, ir.ArrayType) and isinstance(expected, ir.PointerType):
            gv = ir.GlobalVariable(self.module, arg.type, self.module.get_unique_name("str"))
            gv.initializer = arg
            gv.global_constant = True
            return self.builder.bitcast(gv, expected)
        # Pointer -> different pointer: bitcast
        if isinstance(arg.type, ir.PointerType) and isinstance(expected, ir.PointerType):
            return self.builder.bitcast(arg, expected)
        # Numeric conversions
        return self._implicit_convert(arg, expected)

    def codegen_Decl(self, node):

        type_str = ""

        # Static local variables: stored as globals with function-scoped names
        is_static = node.storage and 'static' in node.storage
        if is_static and not self.in_global and isinstance(node.type, c_ast.TypeDecl):
            type_str = node.type.type.names[0]
            ir_type = self._get_ir_type(type_str)
            # Create unique global name
            global_name = f"__static_{self.function.name}_{node.name}"
            gv = ir.GlobalVariable(self.module, ir_type, global_name)
            if node.init:
                init_val = self._eval_const_expr(node.init)
                gv.initializer = ir.Constant(ir_type, init_val)
            else:
                gv.initializer = ir.Constant(ir_type, 0)
            self.define(node.name, (ir_type, gv))
            return None, None

        if isinstance(node.type, c_ast.Enum):
            return self.codegen_Enum(node.type)

        # Forward function declaration: int foo(int x);
        if isinstance(node.type, c_ast.FuncDecl):
            funcname = node.name
            # Skip if function already defined/declared
            existing = self.module.globals.get(funcname)
            if existing and isinstance(existing, ir.Function):
                self.define(funcname, (None, existing))
                return None, None
            ir_type, _ = self.codegen(node.type)
            arg_types = []
            if node.type.args:
                for arg in node.type.args.params:
                    arg_types.append(get_ir_type_from_node(arg))
            func = ir.Function(
                self.module, ir.FunctionType(ir_type, arg_types), name=funcname)
            self.define(funcname, (ir_type, func))
            return None, None

        # Bare struct/union/type definition
        if isinstance(node.type, c_ast.Union):
            if node.name is None:
                self.codegen_Union(node.type)
            return None, None

        if isinstance(node.type, c_ast.Struct) and node.name is None:
            self.codegen_Struct(node.type)
            return None, None

        if isinstance(node.type, c_ast.TypeDecl):
            if isinstance(node.type.type, c_ast.IdentifierType):
                # Check if the type resolves to a struct or pointer via typedef
                resolved = self._resolve_type_str(node.type.type.names[0])
                if isinstance(resolved, (ir.LiteralStructType, ir.PointerType)):
                    name = node.type.declname
                    ir_type = resolved
                    if not self.in_global:
                        ret = self.builder.alloca(ir_type, size=None, name=name)
                        self.define(name, (ir_type, ret))
                    else:
                        ret = ir.GlobalVariable(self.module, ir_type, name)
                        self.define(name, (ir_type, ret))
                    if node.init is not None:
                        init_val, _ = self.codegen(node.init)
                        if init_val.type != ir_type:
                            init_val = self.builder.bitcast(init_val, ir_type)
                        self.builder.store(init_val, ret)
                    return None, None

            if isinstance(node.type.type, (c_ast.Struct, c_ast.Union)):
                name = node.type.declname
                codegen_fn = self.codegen_Union if isinstance(node.type.type, c_ast.Union) else self.codegen_Struct
                if node.type.type.name is None:
                    struct_type = codegen_fn(node.type.type)
                    if not self.in_global:
                        ret = self.builder.alloca(struct_type, size=None, name=name)
                        self.define(name, (struct_type, ret))
                    else:
                        ret = ir.GlobalVariable(self.module, struct_type, name)
                        self.define(name, (struct_type, ret))
                    return None, None
                else:
                    struct_type = self.env[node.type.type.name][0]
                    if not self.in_global:
                        ret = self.builder.alloca(struct_type, size=None, name=name)
                        self.define(name, (struct_type, ret))
                    else:
                        ret = ir.GlobalVariable(self.module, struct_type, name)
                        self.define(name, (struct_type, ret))
                    return None, None
            else:
                type_str = node.type.type.names[0]
                ir_type = self._get_ir_type(type_str)
                type_str = self._resolve_type_str(type_str)
                if isinstance(type_str, ir.Type):
                    type_str = "int"  # fallback for alloca name
                if isinstance(ir_type, ir.DoubleType):
                    init = 0.0
                else:
                    init = 0

                if node.init is not None:
                    if self.in_global:
                        # Global vars: evaluate init as constant expression
                        const_val = self._eval_const_expr(node.init)
                        init_val = ir.Constant(ir_type, const_val)
                    else:
                        init_val, _ = self.codegen(node.init)
                else:
                    init_val = ir.values.Constant(ir_type, init)

                var_addr, var_ir_type = self.create_entry_block_alloca(
                    node.name, type_str, 1)

                if self.in_global:
                    var_addr.initializer = init_val
                else:
                    init_val = self._implicit_convert(init_val, ir_type)
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
                elif isinstance(array_next_type, c_ast.PtrDecl):
                    # Array of pointers: int *arr[3]
                    dim = int(array_node.dim.value)
                    inner = array_next_type.type
                    if isinstance(inner, c_ast.TypeDecl):
                        elem_type_str = inner.type.names[0]
                    else:
                        elem_type_str = "int"
                    elem_ir = ir.PointerType(get_ir_type(elem_type_str))
                    arr_ir = ir.ArrayType(elem_ir, dim)
                    arr_ir.dim_array = [dim]
                    if not self.in_global:
                        var_addr = self.builder.alloca(arr_ir, size=None, name=node.name)
                        self.define(node.name, (arr_ir, var_addr))
                    else:
                        var_addr = ir.GlobalVariable(self.module, arr_ir, node.name)
                        self.define(node.name, (arr_ir, var_addr))
                    return None, var_addr
                else:
                    raise Exception("TODO implement")

            var_addr, var_ir_type = self.create_entry_block_alloca(
                node.name, type_str, 1, array_list)

            # Handle array initialization: int a[3] = {1, 2, 3};
            if node.init is not None and isinstance(node.init, c_ast.InitList):
                elem_ir_type = get_ir_type(type_str)
                if self.in_global:
                    # Global arrays: build constant initializer
                    const_init = self._build_const_array_init(node.init, var_ir_type, elem_ir_type)
                    var_addr.initializer = const_init
                else:
                    self._init_array(var_addr, node.init, elem_ir_type, [ir.Constant(ir.IntType(32), 0)])

        elif isinstance(node.type, c_ast.PtrDecl):

            point_level = 1
            sub_node = node.type
            struct_type = None

            while True:
                sub_next_type = sub_node.type
                if isinstance(sub_next_type, c_ast.TypeDecl):
                    if isinstance(sub_next_type.type, c_ast.Struct):
                        # pointer to struct: struct { int x; } *p
                        struct_type = self.codegen_Struct(sub_next_type.type)
                        type_str = "struct"
                    else:
                        type_str = sub_next_type.type.names[0]
                    break
                elif isinstance(sub_next_type, c_ast.PtrDecl):
                    point_level += 1
                    sub_node = sub_next_type
                    continue
                pass

            if struct_type is not None:
                # Build pointer-to-struct type manually
                ir_type = struct_type
                for _ in range(point_level):
                    ir_type = ir.PointerType(ir_type)
                if not self.in_global:
                    var_addr = self.builder.alloca(ir_type, size=None, name=node.name)
                    self.define(node.name, (ir_type, var_addr))
                else:
                    var_addr = ir.GlobalVariable(self.module, ir_type, node.name)
                    self.define(node.name, (ir_type, var_addr))
                var_ir_type = ir_type
            else:
                var_addr, var_ir_type = self.create_entry_block_alloca(
                    node.name, type_str, 1, point_level=point_level)

            if node.init is not None:
                init_val, _ = self.codegen(node.init)
                if isinstance(init_val.type, ir.ArrayType) and isinstance(var_ir_type, ir.PointerType):
                    # String literal -> pointer: store as global, get pointer
                    gv = ir.GlobalVariable(self.module, init_val.type, self.module.get_unique_name("str"))
                    gv.initializer = init_val
                    gv.global_constant = True
                    init_val = self.builder.bitcast(gv, var_ir_type)
                elif init_val.type != var_ir_type:
                    if isinstance(init_val.type, ir.IntType) and isinstance(var_ir_type, ir.PointerType):
                        # int 0 -> null pointer (NULL)
                        init_val = self.builder.inttoptr(init_val, var_ir_type)
                    else:
                        init_val = self.builder.bitcast(init_val, var_ir_type)
                self.builder.store(init_val, var_addr)
        else:
            return None, None

        return None, var_addr

    def codegen_ID(self, node):

        valtype, var = self.lookup(node.name)
        node.ir_type = valtype
        # Enum constants are stored as ir.Constant, not alloca'd
        if isinstance(var, ir.values.Constant):
            return var, None
        # Array types: decay to pointer to first element
        if isinstance(valtype, ir.ArrayType):
            ptr = self.builder.gep(var, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)], name='arraydecay')
            return ptr, var
        return self.builder.load(var), var

    def codegen_ArrayRef(self, node):

        name = node.name
        subscript = node.subscript
        name_ir, name_ptr = self.codegen(name)
        subscript_ir, subscript_ptr = self.codegen(subscript)

        if isinstance(subscript_ir.type, ir.IntType):
            subscript_ir = self._implicit_convert(subscript_ir, ir.IntType(64))
        else:
            subscript_ir = self.builder.fptoui(subscript_ir, ir.IntType(64))

        # Pointer subscript: p[i] -> *(p + i)
        name_type = getattr(name, 'ir_type', None) or name_ir.type
        if isinstance(name_type, ir.PointerType):
            elem_ptr = self.builder.gep(name_ir, [subscript_ir], name='ptridx')
            value_result = self.builder.load(elem_ptr)
            node.ir_type = name_type
            return value_result, elem_ptr

        # Array subscript: a[i] using byte offset arithmetic
        value_ir_type = name_type.element
        if len(name_type.dim_array) > 1:
            level_lenth = name_type.dim_array[-1] * 8
        else:
            level_lenth = 1 * 8

        dim_lenth = ir.Constant(ir.IntType(64), level_lenth)
        subscript_value_in_array = self.builder.mul(
            dim_lenth, subscript_ir, "array_add")
        name_ptr_int = self.builder.ptrtoint(name_ptr, ir.IntType(64))
        value_ptr = self.builder.add(
            subscript_value_in_array, name_ptr_int, 'addtmp')

        value_ptr = self.builder.inttoptr(
            value_ptr, ir.PointerType(value_ir_type))
        value_result = self.builder.load(value_ptr)

        node.ir_type = name_type.gep(ir.Constant(ir.IntType(64), 0))
        node.ir_type.dim_array = name_type.dim_array[:-1]
        return value_result, value_ptr

    def codegen_Return(self, node):

        if node.expr is None:
            self.builder.ret_void()
        else:
            retval, _ = self.codegen(node.expr)
            # Implicit convert to function return type
            func_ret_type = self.function.return_value.type
            if retval.type != func_ret_type:
                retval = self._implicit_convert(retval, func_ret_type)
            self.builder.ret(retval)
        return None, None

    def codegen_Compound(self, node):


        if node.block_items:
            for stmt in node.block_items:
                if self.builder and self.builder.block.is_terminated:
                    # After a terminator (goto/break/continue/return),
                    # only process labels — skip unreachable code
                    if isinstance(stmt, c_ast.Label):
                        self.codegen(stmt)
                    continue
                self.codegen(stmt)
        return None, None

    def codegen_FuncDecl(self, node):


        if isinstance(node.type, c_ast.PtrDecl):
            return_type_str = node.type.type.type.names[0]
            data_ir_type = get_ir_type(return_type_str)
            ir_type = ir.PointerType(data_ir_type)
        else:
            return_type_str = node.type.type.names[0]
            ir_type = get_ir_type(return_type_str)

        return ir_type, None


    def codegen_FuncDef(self, node):



        # deep level func have deep level
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

            # Reuse forward-declared function if it exists
            existing = self.module.globals.get(funcname)
            if existing and isinstance(existing, ir.Function) and existing.is_declaration:
                self.function = existing
            else:
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
                    var = self.builder.alloca(arg_type, name=p.name)
                    self.define(p.name, (arg_type, var))
                    self.builder.store(self.function.args[i], var)

            self.codegen(node.body)

            if not self.builder.block.is_terminated:
                if isinstance(ir_type, ir.VoidType):
                    self.builder.ret_void()
                else:
                    self.builder.ret(ir.Constant(ir.IntType(64), int(0)))

            return None, None


    def codegen_Struct(self, node):
        # Generate LLVM types for struct members

        # If this is a reference to a named struct without decls, look it up
        if node.name and (node.decls is None or len(node.decls) == 0):
            return self.env[node.name][0]

        member_types = []
        member_names = []
        for decl in node.decls:
            if isinstance(decl.type, c_ast.TypeDecl) and isinstance(decl.type.type, c_ast.Struct):
                nested_type = self.codegen_Struct(decl.type.type)
                member_types.append(nested_type)
            elif isinstance(decl.type, c_ast.ArrayDecl):
                # Array member: int data[3]
                array_node = decl.type
                dim = int(array_node.dim.value)
                elem_type_str = array_node.type.type.names[0]
                member_types.append(ir.ArrayType(get_ir_type(elem_type_str), dim))
            else:
                type_str = decl.type.type.names[0]
                member_types.append(get_ir_type(type_str))
            member_names.append(decl.name)
        # Create the struct type
        struct_type = ir.LiteralStructType(member_types)
        struct_type.members = member_names

        # Register named structs for later reuse
        if node.name:
            self.define(node.name, (struct_type, None))

        return struct_type

    def codegen_Union(self, node):
        """Model union as a byte array of max member size, with member metadata."""
        if node.name and (node.decls is None or len(node.decls) == 0):
            return self.env[node.name][0]

        member_types = {}
        max_size = 0
        for decl in node.decls:
            type_str = decl.type.type.names[0]
            ir_t = get_ir_type(type_str)
            member_types[decl.name] = ir_t
            sz = self._ir_type_size(ir_t)
            if sz > max_size:
                max_size = sz

        # Use an ArrayType of i8 for the union storage
        union_type = ir.ArrayType(int8_t, max_size)
        union_type.members = list(member_types.keys())
        union_type.member_types = member_types
        union_type.is_union = True

        if node.name:
            self.define(node.name, (union_type, None))

        return union_type

    def codegen_StructRef(self, node):

        if isinstance(node.name, c_ast.StructRef):
            # Nested access: s.inner.x — recursively get the inner field addr
            _, inner_addr = self.codegen_StructRef(node.name)
            struct_type = inner_addr.type.pointee
            struct_addr = inner_addr
        elif isinstance(node.name, c_ast.ID):
            struct_instance_addr = self.env[node.name.name][1]
            if not isinstance(struct_instance_addr.type, ir.PointerType):
                raise Exception("Invalid struct reference")

            if node.type == '->':
                ptr_val = self.builder.load(struct_instance_addr)
                struct_type = ptr_val.type.pointee
                struct_addr = ptr_val
            else:
                struct_type = struct_instance_addr.type.pointee
                struct_addr = struct_instance_addr
        else:
            raise Exception(f"Unsupported struct base: {type(node.name)}")

        # Union access: all fields share offset 0, use bitcast
        if getattr(struct_type, 'is_union', False):
            member_ir_type = struct_type.member_types[node.field.name]
            ptr = self.builder.bitcast(struct_addr, ir.PointerType(member_ir_type))
            val = self.builder.load(ptr)
            return val, ptr

        field_index = None
        for i, field in enumerate(struct_type.members):
            if field == node.field.name:
                field_index = i
                break

        if field_index is None:
            raise RuntimeError(f"Field '{node.field.name}' not found in struct")

        field_addr = self.builder.gep(struct_addr, [
            ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)], inbounds=True)

        field_type = struct_type.elements[field_index]
        if isinstance(field_type, ir.ArrayType):
            # Array field: decay to pointer to first element
            elem_ptr = self.builder.gep(field_addr, [
                ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)], name='arraydecay')
            return elem_ptr, field_addr

        field_value = self.builder.load(field_addr)
        return field_value, field_addr

    def codegen_EmptyStatement(self, node):
        return None, None

    def codegen_ExprList(self, node):
        # Comma operator: evaluate all, return last
        result = None
        for expr in node.exprs:
            result, _ = self.codegen(expr)
        return result, None

    def codegen_Label(self, node):
        label_name = f'label_{node.name}'
        # Check if block already created by a forward goto
        if label_name in self.env:
            label_bb = self.env[label_name]
        else:
            label_bb = self.builder.function.append_basic_block(label_name)
            self.define(label_name, label_bb)
        if not self.builder.block.is_terminated:
            self.builder.branch(label_bb)
        self.builder.position_at_end(label_bb)
        if node.stmt:
            self.codegen(node.stmt)
        return None, None

    def codegen_Goto(self, node):
        label_name = f'label_{node.name}'
        if label_name in self.env:
            target_bb = self.env[label_name]
        else:
            # Forward reference: create the block now
            target_bb = self.builder.function.append_basic_block(label_name)
            self.define(label_name, target_bb)
        self.builder.branch(target_bb)
        return None, None

    def codegen_Enum(self, node):
        # Define each enumerator as a constant in the environment
        if node.values:
            current_val = 0
            for enumerator in node.values.enumerators:
                if enumerator.value:
                    current_val = self._eval_const_expr(enumerator.value)
                self.define(enumerator.name, (int64_t, ir.Constant(int64_t, current_val)))
                current_val += 1
        return None, None

    def _eval_const_expr(self, node):
        """Evaluate a constant expression at compile time (for enum values)."""
        if isinstance(node, c_ast.Constant):
            v = node.value.rstrip('uUlL')
            if v.startswith('0x') or v.startswith('0X'):
                return int(v, 16)
            elif v.startswith('0') and len(v) > 1 and v[1:].isdigit():
                return int(v, 8)
            return int(v)
        elif isinstance(node, c_ast.UnaryOp):
            val = self._eval_const_expr(node.expr)
            if node.op == '-':
                return -val
            elif node.op == '+':
                return val
            elif node.op == '~':
                return ~val
        elif isinstance(node, c_ast.BinaryOp):
            l = self._eval_const_expr(node.left)
            r = self._eval_const_expr(node.right)
            ops = {'+': lambda a,b: a+b, '-': lambda a,b: a-b,
                   '*': lambda a,b: a*b, '/': lambda a,b: a//b,
                   '%': lambda a,b: a%b, '<<': lambda a,b: a<<b,
                   '>>': lambda a,b: a>>b, '&': lambda a,b: a&b,
                   '|': lambda a,b: a|b, '^': lambda a,b: a^b}
            return ops[node.op](l, r)
        raise CodegenError(f"Not a constant expression: {type(node).__name__}")

    def codegen_DeclList(self, node):
        for decl in node.decls:
            self.codegen(decl)
        return None, None

    def codegen_Typedef(self, node):
        # typedef int myint; / typedef int* intptr; / typedef struct{...} Name;
        if isinstance(node.type, c_ast.TypeDecl):
            if isinstance(node.type.type, c_ast.IdentifierType):
                base_type = node.type.type.names[0]
                self.define(f'__typedef_{node.name}', base_type)
            elif isinstance(node.type.type, c_ast.Struct):
                struct_type = self.codegen_Struct(node.type.type)
                self.define(f'__typedef_{node.name}', struct_type)
        elif isinstance(node.type, c_ast.PtrDecl):
            # typedef int* intptr;
            inner = node.type.type
            if isinstance(inner, c_ast.TypeDecl):
                base_type_str = inner.type.names[0]
                ptr_type = ir.PointerType(get_ir_type(base_type_str))
                self.define(f'__typedef_{node.name}', ptr_type)
        return None, None
