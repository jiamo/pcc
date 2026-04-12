"""pcc.py_stdlib.math — libm-backed replacement for ``math``.

Every function here is a thin wrapper around a C entry point declared
via :mod:`pcc.extern`. The self-host pipeline lowers each call to a
direct LLVM ``call @<libm-symbol>(...)`` with no Python trampoline.
"""
from __future__ import annotations

from pcc.extern import extern, c_double, c_int, c_int64


# Transcendentals
sqrt_c:  "extern" = extern("sqrt",  (c_double,), c_double)
pow_c:   "extern" = extern("pow",   (c_double, c_double), c_double)
exp_c:   "extern" = extern("exp",   (c_double,), c_double)
log_c:   "extern" = extern("log",   (c_double,), c_double)
log2_c:  "extern" = extern("log2",  (c_double,), c_double)
log10_c: "extern" = extern("log10", (c_double,), c_double)
sin_c:   "extern" = extern("sin",   (c_double,), c_double)
cos_c:   "extern" = extern("cos",   (c_double,), c_double)
tan_c:   "extern" = extern("tan",   (c_double,), c_double)
floor_c: "extern" = extern("floor", (c_double,), c_double)
ceil_c:  "extern" = extern("ceil",  (c_double,), c_double)
fabs_c:  "extern" = extern("fabs",  (c_double,), c_double)
fmod_c:  "extern" = extern("fmod",  (c_double, c_double), c_double)


# Constants (IEEE 754 double). The values are the same libm constants
# math.pi / math.e produce; keeping them inline means the module needs
# no runtime init.
pi:  float = 3.141592653589793
e:   float = 2.718281828459045
tau: float = 6.283185307179586
inf: float = float("inf")


def sqrt(x: float) -> float:
    return sqrt_c(x)


def pow(x: float, y: float) -> float:
    return pow_c(x, y)


def exp(x: float) -> float:
    return exp_c(x)


def log(x: float) -> float:
    return log_c(x)


def log2(x: float) -> float:
    return log2_c(x)


def log10(x: float) -> float:
    return log10_c(x)


def sin(x: float) -> float:
    return sin_c(x)


def cos(x: float) -> float:
    return cos_c(x)


def tan(x: float) -> float:
    return tan_c(x)


def floor(x: float) -> float:
    return floor_c(x)


def ceil(x: float) -> float:
    return ceil_c(x)


def fabs(x: float) -> float:
    return fabs_c(x)


def fmod(x: float, y: float) -> float:
    return fmod_c(x, y)
