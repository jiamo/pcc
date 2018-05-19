from pcc.lex.token import TokenKind
from pcc.lex.lexer import Lexer
from pcc.ast.ast import *

class ParseError(Exception):
    pass


class Parser(object):
    """Parser for the Kaleidoscope language.

    After the parser is created, invoke parse_toplevel multiple times to parse
    Kaleidoscope source into an AST.
    """
    def __init__(self):
        self.token_generator = None
        self.cur_tok = None

    # toplevel ::= definition | external | expression | ';'
    def parse_toplevel(self, buf):
        """Given a string, returns an AST node representing it."""
        self.token_generator = Lexer(buf).tokens()
        self.cur_tok = None
        self._get_next_token()

        if self.cur_tok.kind == TokenKind.EXTERN:
            return self._parse_external()
        elif self.cur_tok.kind == TokenKind.DEF:
            return self._parse_definition()
        elif self._cur_tok_is_operator(';'):
            self._get_next_token()
            return None
        else:
            return self._parse_toplevel_expression()

    def _get_next_token(self):
        self.cur_tok = next(self.token_generator)

    def _match(self, expected_kind, expected_value=None):
        """Consume the current token; verify that it's of the expected kind.

        If expected_kind == TokenKind.OPERATOR, verify the operator's value.
        """
        if (expected_kind == TokenKind.OPERATOR and
            not self._cur_tok_is_operator(expected_value)):
            raise ParseError('Expected "{0}"'.format(expected_value))
        elif expected_kind != self.cur_tok.kind:
            raise ParseError('Expected "{0}"'.format(expected_kind))
        self._get_next_token()

    _precedence_map = {'=': 2, '<': 10, '+': 20, '-': 20, '*': 40}

    def _cur_tok_precedence(self):
        """Get the operator precedence of the current token."""
        try:
            return self._precedence_map[self.cur_tok.value]
        except KeyError:
            return -1

    def _cur_tok_is_operator(self, op):
        """Query whether the current token is the operator op"""
        return (self.cur_tok.kind == TokenKind.OPERATOR and
                self.cur_tok.value == op)

    # identifierexpr
    #   ::= identifier
    #   ::= identifier '(' expression* ')'
    def _parse_identifier_expr(self):
        id_name = self.cur_tok.value
        self._get_next_token()
        # If followed by a '(' it's a call; otherwise, a simple variable ref.
        if not self._cur_tok_is_operator('('):
            return VariableExprAST(id_name)

        self._get_next_token()
        args = []
        if not self._cur_tok_is_operator(')'):
            while True:
                args.append(self._parse_expression())
                if self._cur_tok_is_operator(')'):
                    break
                self._match(TokenKind.OPERATOR, ',')

        self._get_next_token()  # consume the ')'
        return CallExprAST(id_name, args)

    # numberexpr ::= number
    def _parse_number_expr(self):
        result = NumberExprAST(self.cur_tok.value)
        self._get_next_token()  # consume the number
        return result

    # parenexpr ::= '(' expression ')'
    def _parse_paren_expr(self):
        self._get_next_token()  # consume the '('
        expr = self._parse_expression()
        self._match(TokenKind.OPERATOR, ')')
        return expr

    # primary
    #   ::= identifierexpr
    #   ::= numberexpr
    #   ::= parenexpr
    #   ::= ifexpr
    #   ::= forexpr
    def _parse_primary(self):
        if self.cur_tok.kind == TokenKind.IDENTIFIER:
            return self._parse_identifier_expr()
        elif self.cur_tok.kind == TokenKind.NUMBER:
            return self._parse_number_expr()
        elif self._cur_tok_is_operator('('):
            return self._parse_paren_expr()
        elif self.cur_tok.kind == TokenKind.IF:
            return self._parse_if_expr()
        elif self.cur_tok.kind == TokenKind.FOR:
            return self._parse_for_expr()
        elif self.cur_tok.kind == TokenKind.VAR:
            return self._parse_var_expr()
        else:
            raise ParseError('Unknown token when expecting an expression')

    # ifexpr ::= 'if' expression 'then' expression 'else' expression
    def _parse_if_expr(self):
        self._get_next_token()  # consume the 'if'
        cond_expr = self._parse_expression()
        self._match(TokenKind.THEN)
        then_expr = self._parse_expression()
        self._match(TokenKind.ELSE)
        else_expr = self._parse_expression()
        return IfExprAST(cond_expr, then_expr, else_expr)

    # forexpr ::= 'for' identifier '=' expr ',' expr (',' expr)? 'in' expr
    def _parse_for_expr(self):
        self._get_next_token()  # consume the 'for'
        id_name = self.cur_tok.value
        self._match(TokenKind.IDENTIFIER)
        self._match(TokenKind.OPERATOR, '=')
        start_expr = self._parse_expression()
        self._match(TokenKind.OPERATOR, ',')
        end_expr = self._parse_expression()

        # The step part is optional
        if self._cur_tok_is_operator(','):
            self._get_next_token()
            step_expr = self._parse_expression()
        else:
            step_expr = None
        self._match(TokenKind.IN)
        body = self._parse_expression()
        return ForExprAST(id_name, start_expr, end_expr, step_expr, body)

    # varexpr ::= 'var' identifier ('=' expr)?
    #                   (',' identifier ('=' expr)?)* 'in' expr
    def _parse_var_expr(self):
        self._get_next_token()  # consume the 'var'
        vars = []

        # At least one variable name is required
        if self.cur_tok.kind != TokenKind.IDENTIFIER:
            raise ParseError('expected identifier after "var"')
        while True:
            name = self.cur_tok.value
            self._get_next_token()  # consume the identifier

            # Parse the optional initializer
            if self._cur_tok_is_operator('='):
                self._get_next_token()  # consume the '='
                init = self._parse_expression()
            else:
                init = None
            vars.append((name, init))

            # If there are no more vars in this declaration, we're done.
            if not self._cur_tok_is_operator(','):
                break
            self._get_next_token()  # consume the ','
            if self.cur_tok.kind != TokenKind.IDENTIFIER:
                raise ParseError('expected identifier in "var" after ","')

        self._match(TokenKind.IN)
        body = self._parse_expression()
        return VarExprAST(vars, body)

    # unary
    #   ::= primary
    #   ::= <op> unary
    def _parse_unary(self):
        # no unary operator before a primary
        if (not self.cur_tok.kind == TokenKind.OPERATOR or
            self.cur_tok.value in ('(', ',')):
            return self._parse_primary()

        # unary operator
        op = self.cur_tok.value
        self._get_next_token()
        return UnaryExprAST(op, self._parse_unary())

    # binoprhs ::= (<binop> primary)*
    def _parse_binop_rhs(self, expr_prec, lhs):
        """Parse the right-hand-side of a binary expression.

        expr_prec: minimal precedence to keep going (precedence climbing).
        lhs: AST of the left-hand-side.
        """
        while True:
            cur_prec = self._cur_tok_precedence()
            # If this is a binary operator with precedence lower than the
            # currently parsed sub-expression, bail out. If it binds at least
            # as tightly, keep going.
            # Note that the precedence of non-operators is defined to be -1,
            # so this condition handles cases when the expression ended.
            if cur_prec < expr_prec:
                return lhs
            op = self.cur_tok.value
            self._get_next_token()  # consume the operator
            rhs = self._parse_unary()

            next_prec = self._cur_tok_precedence()
            # There are three options:
            # 1. next_prec > cur_prec: we need to make a recursive call
            # 2. next_prec == cur_prec: no need for a recursive call, the next
            #    iteration of this loop will handle it.
            # 3. next_prec < cur_prec: no need for a recursive call, combine
            #    lhs and the next iteration will immediately bail out.
            if cur_prec < next_prec:
                rhs = self._parse_binop_rhs(cur_prec + 1, rhs)

            # Merge lhs/rhs
            lhs = BinaryExprAST(op, lhs, rhs)

    # expression ::= primary binoprhs
    def _parse_expression(self):
        lhs = self._parse_unary()
        # Start with precedence 0 because we want to bind any operator to the
        # expression at this point.
        return self._parse_binop_rhs(0, lhs)

    # prototype
    #   ::= id '(' id* ')'
    #   ::= 'binary' LETTER number? '(' id id ')'
    def _parse_prototype(self):
        prec = 30
        if self.cur_tok.kind == TokenKind.IDENTIFIER:
            name = self.cur_tok.value
            self._get_next_token()
        elif self.cur_tok.kind == TokenKind.UNARY:
            self._get_next_token()
            if self.cur_tok.kind != TokenKind.OPERATOR:
                raise ParseError('Expected operator after "unary"')
            name = 'unary{0}'.format(self.cur_tok.value)
            self._get_next_token()
        elif self.cur_tok.kind == TokenKind.BINARY:
            self._get_next_token()
            if self.cur_tok.kind != TokenKind.OPERATOR:
                raise ParseError('Expected operator after "binary"')
            name = 'binary{0}'.format(self.cur_tok.value)
            self._get_next_token()

            # Try to parse precedence
            if self.cur_tok.kind == TokenKind.NUMBER:
                prec = int(self.cur_tok.value)
                if not (0 < prec < 101):
                    raise ParseError('Invalid precedence', prec)
                self._get_next_token()

            # Add the new operator to our precedence table so we can properly
            # parse it.
            self._precedence_map[name[-1]] = prec

        self._match(TokenKind.OPERATOR, '(')
        argnames = []
        while self.cur_tok.kind == TokenKind.IDENTIFIER:
            argnames.append(self.cur_tok.value)
            self._get_next_token()
        self._match(TokenKind.OPERATOR, ')')

        if name.startswith('binary') and len(argnames) != 2:
            raise ParseError('Expected binary operator to have 2 operands')
        elif name.startswith('unary') and len(argnames) != 1:
            raise ParseError('Expected unary operator to have one operand')

        return PrototypeAST(
            name, argnames, name.startswith(('unary', 'binary')), prec)

    # external ::= 'extern' prototype
    def _parse_external(self):
        self._get_next_token()  # consume 'extern'
        return self._parse_prototype()

    # definition ::= 'def' prototype expression
    def _parse_definition(self):
        self._get_next_token()  # consume 'def'
        proto = self._parse_prototype()
        expr = self._parse_expression()
        return FunctionAST(proto, expr)

    # toplevel ::= expression
    def _parse_toplevel_expression(self):
        expr = self._parse_expression()
        return FunctionAST.create_anonymous(expr)
