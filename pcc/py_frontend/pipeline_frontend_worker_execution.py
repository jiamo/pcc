"""Execution protocol for isolated multi-module frontend workers."""

from __future__ import annotations

import os
import sys
import time


def run_export_worker(
    manifest,
    *,
    worker_timing_enabled,
    build_closed_world_context,
    write_ast_wire,
    closed_world_reexport_edges,
    mark_function_object_exports,
    write_native_exports_wire,
    write_reexport_edges_wire,
) -> int:
    worker_timing = worker_timing_enabled()
    total_started = time.monotonic() if worker_timing else 0.0
    result_path = str(manifest["result_path"])
    ir_dir = str(manifest["ir_dir"])
    ast_dir = str(manifest.get("ast_dir", "") or "")
    src_paths = manifest["src_paths"]
    module_names = manifest["module_names"]
    assigned_indices = manifest["assigned_indices"]
    subset_srcs = []
    subset_names = []
    for index in assigned_indices:
        subset_srcs.append(src_paths[index])
        subset_names.append(module_names[index])
    parsed_modules, native_exports, _derived_class_map = build_closed_world_context(
        subset_srcs,
        subset_names,
        profile=None,
        lift_indices=None,
        merge_exports=False,
    )
    if ast_dir:
        for local_index, ast_module in enumerate(parsed_modules):
            index = assigned_indices[local_index]
            ast_path = os.path.join(ast_dir, "module_" + str(index) + ".json")
            write_ast_wire(ast_path, ast_module)
    edges = closed_world_reexport_edges(
        parsed_modules,
        subset_names,
        subset_srcs,
        module_names,
    )
    function_object_uses = mark_function_object_exports(
        parsed_modules,
        subset_names,
        subset_srcs,
        native_exports,
        known_module_names=module_names,
    )
    exports_path = os.path.join(
        ir_dir,
        "exports_" + os.path.basename(result_path) + ".json",
    )
    edges_path = os.path.join(
        ir_dir,
        "reexports_" + os.path.basename(result_path) + ".json",
    )
    write_native_exports_wire(
        exports_path,
        native_exports,
        {},
        function_object_uses=function_object_uses,
    )
    write_reexport_edges_wire(edges_path, edges)
    with open(result_path, "w", encoding="utf-8") as stream:
        line = "EXPORT\t" + exports_path + "\t" + edges_path
        if worker_timing:
            total_ms = int((time.monotonic() - total_started) * 1000)
            line += "\t" + str(total_ms)
        stream.write(line + "\n")
    return 0


def run_codegen_worker(
    manifest_path: str,
    *,
    read_manifest,
    run_export_worker_callback,
    worker_timing_enabled,
    read_native_exports_wire,
    read_ast_wire,
    build_closed_world_context,
    module_imports_native_extension,
    contextual_host_params_for_module,
    module_uses_default_native_exports,
    copy_native_module_exports,
    closed_world_function_object_exports,
    log,
    ir_needs_libpython,
    safe_exception_text,
    write_worker_error,
    pipeline_error,
) -> int:
    result_path = ""
    try:
        manifest = read_manifest(manifest_path)
        result_path = str(manifest["result_path"])
        job_kind = str(manifest.get("job_kind", "codegen"))
        if job_kind == "export":
            return run_export_worker_callback(manifest)
        from .type_infer import infer_module
        from .codegen.layer1 import L1CodeGen

        src_paths = manifest["src_paths"]
        module_names = manifest["module_names"]
        entry_module = str(manifest["entry_module"])
        sibling_inits = tuple(manifest["sibling_inits"])
        libpython_mode = str(manifest["libpython_mode"])
        ir_scaffold_mode = str(manifest["ir_scaffold_mode"])
        verbose = bool(manifest["verbose"])
        assigned_indices = manifest["assigned_indices"]
        ir_dir = str(manifest["ir_dir"])
        exports_path = str(manifest.get("exports_path", "") or "")
        ast_dir = str(manifest.get("ast_dir", "") or "")
        worker_timing = worker_timing_enabled()
        if exports_path:
            native_exports, derived_class_map = read_native_exports_wire(exports_path)
            parsed_modules = [None for _source in src_paths]
            parse_ms_by_index = {}
            if ast_dir:
                for index in assigned_indices:
                    parse_started = time.monotonic() if worker_timing else 0.0
                    ast_path = os.path.join(
                        ast_dir,
                        "module_" + str(index) + ".json",
                    )
                    parsed_modules[index] = read_ast_wire(ast_path)
                    if worker_timing:
                        parse_ms_by_index[index] = int(
                            (time.monotonic() - parse_started) * 1000
                        )
            else:
                from ..parse.py_lift import parse_and_lift

                for index in assigned_indices:
                    source_path = src_paths[index]
                    module_name = module_names[index]
                    parse_started = time.monotonic() if worker_timing else 0.0
                    with open(source_path, "r", encoding="utf-8") as stream:
                        source = stream.read()
                    parsed_modules[index] = parse_and_lift(
                        source,
                        source_path,
                        module_name,
                    )
                    if worker_timing:
                        parse_ms_by_index[index] = int(
                            (time.monotonic() - parse_started) * 1000
                        )
        else:
            parse_ms_by_index = {}
            parsed_modules, native_exports, derived_class_map = (
                build_closed_world_context(
                    src_paths,
                    module_names,
                    profile=None,
                    lift_indices=assigned_indices,
                )
            )

        result_lines: list[str] = []
        for index in assigned_indices:
            module_name = module_names[index]
            if worker_timing:
                sys.stderr.write(
                    "pcc frontend worker start index="
                    + str(index)
                    + " module="
                    + module_name
                    + "\n"
                )
            ast_module = parsed_modules[index]
            needs_native_extension_exports = module_imports_native_extension(
                ast_module,
                native_modules=module_names,
                ir_scaffold_mode=ir_scaffold_mode,
            )
            external_for_this = {}
            for owner_name, exports in native_exports.items():
                if owner_name != module_name:
                    external_for_this[owner_name] = exports
            infer_ms = 0
            try:
                infer_started = time.monotonic() if worker_timing else 0.0
                typed_module = infer_module(
                    ast_module,
                    external_exports=external_for_this,
                    derived_class_map=derived_class_map,
                    contextual_host_params=contextual_host_params_for_module(
                        ast_module,
                        module_name,
                    ),
                )
                if worker_timing:
                    infer_ms = int((time.monotonic() - infer_started) * 1000)
                    sys.stderr.write(
                        "pcc frontend worker inferred index="
                        + str(index)
                        + " module="
                        + module_name
                        + " infer_ms="
                        + str(infer_ms)
                        + "\n"
                    )
            except Exception as exc:
                raise pipeline_error(
                    "type_infer["
                    + module_name
                    + "]: "
                    + type(exc).__name__
                    + ": "
                    + safe_exception_text(exc)
                ) from exc
            try:
                codegen = L1CodeGen(
                    typed_module,
                    libpython_mode == "on",
                    ir_scaffold_mode,
                )
                codegen._strict_no_libpython = libpython_mode == "off"
                codegen._prefer_native_callable_values = libpython_mode == "off"
                codegen._module_source_path = os.path.abspath(src_paths[index])
                codegen._skip_program_main = module_name != entry_module
                codegen._sibling_module_inits = sibling_inits
                if module_uses_default_native_exports(module_name):
                    codegen_exports = copy_native_module_exports(
                        codegen._native_module_exports
                    )
                else:
                    codegen_exports = {}
                for owner_name, exports in native_exports.items():
                    if owner_name != module_name:
                        codegen_exports[owner_name] = exports
                codegen._native_module_exports = codegen_exports
                codegen._native_function_object_exports = (
                    closed_world_function_object_exports(
                        native_exports,
                        module_name,
                    )
                )
            except Exception as exc:
                raise pipeline_error(
                    "codegen_prepare["
                    + module_name
                    + "]: "
                    + type(exc).__name__
                    + ": "
                    + safe_exception_text(exc)
                ) from exc
            if verbose:
                log(verbose, "worker codegen[" + module_name + "]")
            codegen_ms = 0
            try:
                codegen_started = time.monotonic() if worker_timing else 0.0
                ir_text = str(codegen.generate(typed_module))
                if worker_timing:
                    codegen_ms = int((time.monotonic() - codegen_started) * 1000)
            except Exception as exc:
                raise pipeline_error(
                    "codegen["
                    + module_name
                    + "]: "
                    + type(exc).__name__
                    + ": "
                    + safe_exception_text(exc)
                ) from exc
            ir_path = os.path.join(ir_dir, "module_" + str(index) + ".ll")
            with open(ir_path, "w", encoding="utf-8") as stream:
                stream.write(ir_text)
            result_line = (
                "OK\t"
                + str(index)
                + "\t"
                + module_name
                + "\t"
                + ("1" if ir_needs_libpython(ir_text) else "0")
                + "\t"
                + ("1" if needs_native_extension_exports else "0")
                + "\t"
                + str(len(ir_text))
                + "\t"
                + ir_path
            )
            if worker_timing:
                result_line += (
                    "\t"
                    + str(parse_ms_by_index.get(index, 0))
                    + "\t"
                    + str(infer_ms)
                    + "\t"
                    + str(codegen_ms)
                )
                sys.stderr.write(
                    "pcc frontend worker done index="
                    + str(index)
                    + " module="
                    + module_name
                    + " infer_ms="
                    + str(infer_ms)
                    + " codegen_ms="
                    + str(codegen_ms)
                    + "\n"
                )
            result_lines.append(result_line)
        with open(result_path, "w", encoding="utf-8") as stream:
            for line in result_lines:
                stream.write(line + "\n")
        return 0
    except Exception as exc:
        exc_type = type(exc).__name__
        if exc_type is None:
            exc_type = "Exception"
        message = exc_type + ": " + safe_exception_text(exc)
        if result_path:
            try:
                write_worker_error(result_path, message)
            except Exception:
                pass
        try:
            sys.stderr.write("pcc frontend worker failed: " + message + "\n")
        except Exception:
            pass
        return 1
