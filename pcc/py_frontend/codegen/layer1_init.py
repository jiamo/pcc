"""Constructor-state initialization for ``L1CodeGen``."""
from __future__ import annotations

import os
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import FuncDef, Module, Type
from .class_gen import ClassLowering
from .layer1_support import _default_native_module_exports
from .runtime_abi import declare_runtime


class Layer1InitMixin:
    def _init_l1_state(
        self,
        module: Module,
        emit_cpy_main_exitcode: bool,
        ir_scaffold_mode: str,
    ) -> None:
        self.ast_module = module
        self._ast_body = module.body
        self._try_err_block = None
        self._finally_stack = []
        self._emitting_finally = False
        self._prefer_native_callable_values = False
        self._cpy_values = set()
        self.emit_cpy_main_exitcode = emit_cpy_main_exitcode
        if ir_scaffold_mode not in ("off", "on"):
            raise ValueError(
                "invalid ir_scaffold_mode "
                + repr(ir_scaffold_mode)
                + "; expected 'off' or 'on'"
            )
        self.ir_scaffold_mode = ir_scaffold_mode
        self.module = ir.Module(name=module.name or "pcc_py_module")
        self.runtime: dict[str, ir.Function] = declare_runtime(self.module)
        self._codegen_trace_enabled = bool(
            os.environ.get("PCC_DEBUG_CODEGEN_PHASES")
        )
        self._codegen_trace_capacity = 64
        self._codegen_trace_ring: list[tuple[str, str, str, str, str, str, str]] = []
        self._codegen_trace_next: int = 0
        self._codegen_trace_diagnosed: bool = False
        self._codegen_current_stmt_index: int = -1
        self._codegen_current_stmt_kind: str = ""
        self._codegen_current_expr_kind: str = ""
        self._codegen_current_module_name: str = module.name or "<module>"
        self._debug_release_checks = bool(
            os.environ.get("PCC_DEBUG_RELEASES", "").strip()
            or os.environ.get("PCC_DEBUG_RUNTIME", "").strip()
        )
        self._printf = self._declare_printf()
        self.functions: dict[str, ir.Function] = {}
        self._c_abi_export_symbols: set[str] = set()
        self._module_has_c_abi_export = False
        self._fn_err_exit_blocks: dict[str, ir.Block] = {}
        self._fn_err_exit_gc_root_names: dict[str, set[str]] = {}
        self._post_call_frame_blocks: dict[
            tuple[str, str, str, str, int],
            ir.Block,
        ] = {}
        self._funcdef_yield_sentinel_cache: dict[int, bool] = {}
        self._generator_ctx_stack: list = []
        self._generator_func_names: set[str] = set()
        self.builder: Optional[ir.IRBuilder] = None
        self.current_function: Optional[ir.Function] = None
        self.current_func_def: Optional[FuncDef] = None
        self.current_class = None
        self.current_method_kind = None
        self.class_lowering: ClassLowering = ClassLowering(self)
        self._current_global_names: set[str] = set()
        self.env: dict[str, tuple[ir.AllocaInstr, ir.Type, Type]] = {}
        self._module_globals: dict[str, tuple[ir.GlobalVariable, Type]] = {}
        self._cpy_module_flags: dict[str, bool] = {}
        self.env_class_hint: dict[str, str] = {}
        self.env_class_object_hint: dict[str, str] = {}
        self._literal_dict_expr_bindings: dict[str, object] = {}
        self.env_list_elem_class_hint: dict[str, str] = {}
        self._ir_builder_env_flags: dict[str, bool] = {}
        self._class_aliases: dict[str, str] = {}
        self._module_uses_raw_int_scaffold = False
        self._box_int_locals = False
        self._exact_int_env_flags: dict[str, bool] = {}
        self.loop_stack: list[tuple[ir.Block, ir.Block]] = []
        self._fmt_int: Optional[ir.GlobalVariable] = None
        self._fmt_float: Optional[ir.GlobalVariable] = None
        self._fmt_bool_true: Optional[ir.GlobalVariable] = None
        self._fmt_bool_false: Optional[ir.GlobalVariable] = None
        self._str_pool: dict[str, ir.GlobalVariable] = {}
        self._str_obj_pool: dict[str, ir.GlobalVariable] = {}
        self._attr_pool: dict[str, ir.GlobalVariable] = {}
        self._cstr_pool: dict[str, ir.GlobalVariable] = {}
        self._str_counter = 0
        self._cstr_counter = 0
        self._class_type_export_cache: dict[tuple[str, str], Optional[str]] = {}
        self._tmp_counter = 0
        self._skip_program_main: bool = False
        self._sibling_module_inits: tuple[str, ...] = ()
        self._native_module_exports: Optional[dict] = _default_native_module_exports(
            module.name
        )
        self._native_module_aliases: dict[str, str] = {}
        self._native_module_object_aliases: dict[str, str] = {}
        self._native_extension_module_env: dict[str, ir.GlobalVariable] = {}
        self._native_builtin_module_aliases: dict[str, str] = {}
        self._native_builtin_value_aliases: dict[str, str] = {}
        self._typing_typevar_aliases: dict[str, str] = {}
        self._typing_optional_aliases: dict[str, str] = {}
        self._inspect_signature_aliases: dict = {}
        self._inspect_fullargspec_aliases: dict = {}
        self._native_module_attr_globals: dict[
            tuple[str, str],
            ir.GlobalVariable,
        ] = {}
        self._native_file_values: set = set()
        self._native_file_env_flags: dict[str, bool] = {}
        self._native_fileinput_values: set = set()
        self._native_fileinput_env_flags: dict[str, bool] = {}
        self._threading_env_flags: dict[str, str] = {}
        self._threading_list_elem_flags: dict[str, str] = {}
        self._thread_safepoints_enabled = bool(
            str(os.environ.get("PCC_WITH_THREADS", "")).strip()
        )
        self._runtime_threads_enabled = (
            self._thread_safepoints_enabled
            or self._module_imports_threading(module)
        )
        self._weak_dict_env_flags: dict[str, str] = {}
        self._cpy_env_flags: dict[str, bool] = {}
        self._cross_module_func_defs: dict[str, FuncDef] = {}
        self._module_block_func_defs: dict[str, FuncDef] = {}
        self._owned_local_names: set[str] = set()
        self._owned_local_has_value: set[str] = set()
        self._owned_local_flag_slots: dict[str, ir.Value] = {}
        self._gc_rooted_local_names: set[str] = set()
        # Names bound by `except ... as <name>`. A local assigned the value of
        # one of these (e.g. `saved = e`) borrows a caught exception whose only
        # surviving reference is that local, so it must be GC-rooted or the
        # tracing collect sweeps the exception's referents. See
        # gc-5backend-exception-referent-roots-no-libpython.md.
        self._except_binding_names: set[str] = set()
        self._current_param_names: set[str] = set()
        self._unboxed_typed_int_abi_cache: dict[str, bool] = {}
        self._typed_int_abi_call_arg_safety: list[tuple[str, list[bool]]] = []
        self._hoisted_capture_params = {}
        self._hoisted_class_capture_params = {}
        self._hoisted_enclosing_class = {}
        self._hoisted_enclosing_method_kind = {}
        self._closure_boxed_params = {}
        self._hoist_wrap_caps = {}
