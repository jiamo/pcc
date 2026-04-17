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

int py_runtime_program_argc = 0;
const char **py_runtime_program_argv = NULL;
void (*py_runtime_program_args_hook)(void) = NULL;

void py_set_program_args(int argc, const char **argv) {
    py_runtime_program_argc = argc > 0 ? argc : 0;
    py_runtime_program_argv = argv;
    if (py_runtime_program_args_hook != NULL) {
        py_runtime_program_args_hook();
    }
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
