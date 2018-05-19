
# AST hierarchy
class ASTNode(object):
    def dump(self, indent=0):
        raise NotImplementedError


class ExprAST(ASTNode):
    pass


class NumberExprAST(ExprAST):
    def __init__(self, val):
        self.val = val

    def dump(self, indent=0):
        return '{0}{1}[{2}]'.format(
            ' ' * indent, self.__class__.__name__, self.val)


class VariableExprAST(ExprAST):
    def __init__(self, name):
        self.name = name

    def dump(self, indent=0):
        return '{0}{1}[{2}]'.format(
            ' ' * indent, self.__class__.__name__, self.name)


class VarExprAST(ExprAST):
    def __init__(self, vars, body):
        # vars is a sequence of (name, init) pairs
        self.vars = vars
        self.body = body

    def dump(self, indent=0):
        prefix = ' ' * indent
        s = '{0}{1}\n'.format(prefix, self.__class__.__name__)
        for name, init in self.vars:
            s += '{0} {1}'.format(prefix, name)
            if init is None:
                s += '\n'
            else:
                s += '=\n' + init.dump(indent+2) + '\n'
        s += '{0} Body:\n'.format(prefix)
        s += self.body.dump(indent + 2)
        return s


class UnaryExprAST(ExprAST):
    def __init__(self, op, operand):
        self.op = op
        self.operand = operand

    def dump(self, indent=0):
        s = '{0}{1}[{2}]\n'.format(
            ' ' * indent, self.__class__.__name__, self.op)
        s += self.operand.dump(indent + 2)
        return s


class BinaryExprAST(ExprAST):
    def __init__(self, op, lhs, rhs):
        self.op = op
        self.lhs = lhs
        self.rhs = rhs

    def dump(self, indent=0):
        s = '{0}{1}[{2}]\n'.format(
            ' ' * indent, self.__class__.__name__, self.op)
        s += self.lhs.dump(indent + 2) + '\n'
        s += self.rhs.dump(indent + 2)
        return s


class IfExprAST(ExprAST):
    def __init__(self, cond_expr, then_expr, else_expr):
        self.cond_expr = cond_expr
        self.then_expr = then_expr
        self.else_expr = else_expr

    def dump(self, indent=0):
        prefix = ' ' * indent
        s = '{0}{1}\n'.format(prefix, self.__class__.__name__)
        s += '{0} Condition:\n{1}\n'.format(
            prefix, self.cond_expr.dump(indent + 2))
        s += '{0} Then:\n{1}\n'.format(
            prefix, self.then_expr.dump(indent + 2))
        s += '{0} Else:\n{1}'.format(
            prefix, self.else_expr.dump(indent + 2))
        return s


class ForExprAST(ExprAST):
    def __init__(self, id_name, start_expr, end_expr, step_expr, body):
        self.id_name = id_name
        self.start_expr = start_expr
        self.end_expr = end_expr
        self.step_expr = step_expr
        self.body = body

    def dump(self, indent=0):
        prefix = ' ' * indent
        s = '{0}{1}\n'.format(prefix, self.__class__.__name__)
        s += '{0} Start [{1}]:\n{2}\n'.format(
            prefix, self.id_name, self.start_expr.dump(indent + 2))
        s += '{0} End:\n{1}\n'.format(
            prefix, self.end_expr.dump(indent + 2))
        s += '{0} Step:\n{1}\n'.format(
            prefix, self.step_expr.dump(indent + 2))
        s += '{0} Body:\n{1}\n'.format(
            prefix, self.body.dump(indent + 2))
        return s


class CallExprAST(ExprAST):
    def __init__(self, callee, args):
        self.callee = callee
        self.args = args

    def dump(self, indent=0):
        s = '{0}{1}[{2}]\n'.format(
            ' ' * indent, self.__class__.__name__, self.callee)
        for arg in self.args:
            s += arg.dump(indent + 2) + '\n'
        return s[:-1]  # snip out trailing '\n'


class PrototypeAST(ASTNode):
    def __init__(self, name, argnames, isoperator=False, prec=0):
        self.name = name
        self.argnames = argnames
        self.isoperator = isoperator
        self.prec = prec

    def is_unary_op(self):
        return self.isoperator and len(self.argnames) == 1

    def is_binary_op(self):
        return self.isoperator and len(self.argnames) == 2

    def get_op_name(self):
        assert self.isoperator
        return self.name[-1]

    def dump(self, indent=0):
        s = '{0}{1} {2}({3})'.format(
            ' ' * indent, self.__class__.__name__, self.name,
            ', '.join(self.argnames))
        if self.isoperator:
            s += '[operator with prec={0}]'.format(self.prec)
        return s


class FunctionAST(ASTNode):
    def __init__(self, proto, body):
        self.proto = proto
        self.body = body

    _anonymous_function_counter = 0

    @classmethod
    def create_anonymous(klass, expr):
        """Create an anonymous function to hold an expression."""
        klass._anonymous_function_counter += 1
        return klass(
            PrototypeAST('_anon{0}'.format(klass._anonymous_function_counter),
                         []),
            expr)

    def is_anonymous(self):
        return self.proto.name.startswith('_anon')

    def dump(self, indent=0):
        s = '{0}{1}[{2}]\n'.format(
            ' ' * indent, self.__class__.__name__, self.proto.dump())
        s += self.body.dump(indent + 2) + '\n'
        return s

