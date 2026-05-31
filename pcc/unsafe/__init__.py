"""Compiler-recognized unsafe intrinsics for pcc runtime code.

This module is intentionally not a normal runtime library. The
pcc-Python frontend consumes imports from ``pcc.unsafe`` at compile
time and lowers each call to raw LLVM/platform operations. Calling
these helpers under CPython is a misuse and raises loudly.
"""
from __future__ import annotations

from typing import Any


def _trap(name: str) -> None:
    raise NotImplementedError(
        f"pcc.unsafe.{name}() must be lowered by the pcc compiler"
    )


def malloc(size: int) -> Any:
    _trap("malloc")


def cstr(value: str) -> Any:
    _trap("cstr")


def global_addr(symbol: str) -> Any:
    _trap("global_addr")


def global_load_ptr(symbol: str) -> Any:
    _trap("global_load_ptr")


def global_store_ptr(symbol: str, value: Any) -> None:
    _trap("global_store_ptr")


def define_global_ptr_null(symbol: str) -> None:
    _trap("define_global_ptr_null")


def calloc(count: int, size: int) -> Any:
    _trap("calloc")


def realloc(ptr: Any, size: int) -> Any:
    _trap("realloc")


def free(ptr: Any) -> None:
    _trap("free")


def ptr_add(ptr: Any, offset: int) -> Any:
    _trap("ptr_add")


def ptr_diff(lhs: Any, rhs: Any) -> int:
    _trap("ptr_diff")


def null() -> Any:
    _trap("null")


def ptr_eq(lhs: Any, rhs: Any) -> bool:
    _trap("ptr_eq")


def ptr_is_null(ptr: Any) -> bool:
    _trap("ptr_is_null")


def is_tagged_int(ptr: Any) -> bool:
    _trap("is_tagged_int")


def tag_int(value: int) -> Any:
    _trap("tag_int")


def untag_int(ptr: Any) -> int:
    _trap("untag_int")


def load_i64(ptr: Any, offset: int) -> int:
    _trap("load_i64")


def load_i32(ptr: Any, offset: int) -> int:
    _trap("load_i32")


def load_i8(ptr: Any, offset: int) -> int:
    _trap("load_i8")


def load_ptr(ptr: Any, offset: int) -> Any:
    _trap("load_ptr")


def load_f64(ptr: Any, offset: int) -> float:
    _trap("load_f64")


def store_i64(ptr: Any, offset: int, value: int) -> None:
    _trap("store_i64")


def store_i32(ptr: Any, offset: int, value: int) -> None:
    _trap("store_i32")


def store_i8(ptr: Any, offset: int, value: int) -> None:
    _trap("store_i8")


def store_ptr(ptr: Any, offset: int, value: Any) -> None:
    _trap("store_ptr")


def store_f64(ptr: Any, offset: int, value: float) -> None:
    _trap("store_f64")


def memset(ptr: Any, value: int, size: int) -> Any:
    _trap("memset")


def memcpy(dst: Any, src: Any, size: int) -> Any:
    _trap("memcpy")


def memmove(dst: Any, src: Any, size: int) -> Any:
    _trap("memmove")


def write(fd: int, ptr: Any, size: int) -> int:
    _trap("write")


def strlen(ptr: Any) -> int:
    _trap("strlen")


def getenv(name: Any) -> Any:
    _trap("getenv")


def setenv(name: Any, value: Any, overwrite: int) -> int:
    _trap("setenv")


def unsetenv(name: Any) -> int:
    _trap("unsetenv")


def access(path: Any, mode: int) -> int:
    _trap("access")


def stat_kind(path: Any) -> int:
    _trap("stat_kind")


def stat_mtime(path: Any) -> float:
    _trap("stat_mtime")


def target_sys_platform() -> Any:
    _trap("target_sys_platform")


def target_platform_machine() -> Any:
    _trap("target_platform_machine")


def call_ptr1(fn: Any, arg0: Any) -> Any:
    _trap("call_ptr1")


def call_void_ptr1(fn: Any, arg0: Any) -> None:
    _trap("call_void_ptr1")


def call_ptr2(fn: Any, arg0: Any, arg1: Any) -> Any:
    _trap("call_ptr2")


__all__ = [
    "malloc",
    "cstr",
    "global_addr",
    "global_load_ptr",
    "global_store_ptr",
    "define_global_ptr_null",
    "calloc",
    "realloc",
    "free",
    "ptr_add",
    "ptr_diff",
    "null",
    "ptr_eq",
    "ptr_is_null",
    "is_tagged_int",
    "tag_int",
    "untag_int",
    "load_i64",
    "load_i32",
    "load_i8",
    "load_ptr",
    "load_f64",
    "store_i64",
    "store_i32",
    "store_i8",
    "store_ptr",
    "store_f64",
    "memset",
    "memcpy",
    "memmove",
    "write",
    "strlen",
    "getenv",
    "setenv",
    "unsetenv",
    "access",
    "stat_kind",
    "stat_mtime",
    "target_sys_platform",
    "target_platform_machine",
    "call_ptr1",
    "call_void_ptr1",
    "call_ptr2",
]
