/* pcc/py_runtime/src/py_process.c
 *
 * Process-level runtime state that is independent of the optional
 * CPython fallback.  Keep this outside py_libpython.c so the default
 * no-libpython archives do not need to carry any CPython shim object.
 */

#include "py_runtime.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PCC_PYTHON_ARGV0_MARKER "--pcc-internal-python-argv0-v1"

int py_runtime_program_argc = 0;
const char **py_runtime_program_argv = NULL;
const char *py_runtime_program_executable = NULL;
int32_t py_runtime_program_mode = 0;
void (*py_runtime_program_args_hook)(void) = NULL;

void py_set_program_args(int argc, const char **argv) {
    py_runtime_program_executable = (
        argc > 0 && argv != NULL && argv[0] != NULL ? argv[0] : ""
    );
    py_runtime_program_mode = 0;
    if (
        argc >= 4
        && argv != NULL
        && argv[1] != NULL
        && strcmp(argv[1], PCC_PYTHON_ARGV0_MARKER) == 0
    ) {
        const char *mode = argv[2] != NULL ? argv[2] : "";
        int32_t recognized_mode = 0;
        if (strcmp(mode, "script") == 0) recognized_mode = 1;
        else if (strcmp(mode, "module") == 0) recognized_mode = 2;
        else if (strcmp(mode, "command") == 0) recognized_mode = 3;
        else if (strcmp(mode, "stdin") == 0) recognized_mode = 4;
        if (recognized_mode != 0) {
            py_runtime_program_mode = recognized_mode;
            argc -= 3;
            argv += 3;
        }
    }
    py_runtime_program_argc = argc > 0 ? argc : 0;
    py_runtime_program_argv = argv;
    if (py_runtime_program_args_hook != NULL) {
        py_runtime_program_args_hook();
    }
}

const char *py_program_executable(void) {
    return py_runtime_program_executable != NULL
        ? py_runtime_program_executable
        : "";
}

int32_t py_program_mode(void) {
    return py_runtime_program_mode;
}

int64_t py_program_argc(void) {
    return py_runtime_program_argc > 0
        ? (int64_t)py_runtime_program_argc
        : 0;
}

const char *py_program_argv(int64_t index) {
    if (
        index < 0
        || index >= py_runtime_program_argc
        || py_runtime_program_argv == NULL
    ) {
        return NULL;
    }
    const char *arg = py_runtime_program_argv[index];
    return arg != NULL ? arg : "";
}

void py_process_exit(int64_t code) {
    fflush(stdout);
    fflush(stderr);
    exit((int)code);
}
