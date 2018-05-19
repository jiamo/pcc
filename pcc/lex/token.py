from collections import namedtuple
from enum import Enum


# Each token is a tuple of kind and value. kind is one of the enumeration values
# in TokenKind. value is the textual value of the token in the input.
class TokenKind(Enum):
    EOF = -1
    DEF = -2
    EXTERN = -3
    IDENTIFIER = -4
    NUMBER = -5
    OPERATOR = -6
    IF = -7
    THEN = -8
    ELSE = -9
    FOR = -10
    IN = -11
    BINARY = -12
    UNARY = -13
    VAR = -14


Token = namedtuple('Token', 'kind value')