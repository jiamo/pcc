/* Shared subprocess timeout primitive for the C and pcc-Python runtimes.
 *
 * Process creation, process groups, signals, and waitpid are C-kernel duties;
 * keep one implementation here instead of duplicating them in the semantic
 * pcc-Python runtime. The child gets its own process group so timeout cleanup
 * reaches grandchildren as well as the immediate command.
 */

#include "py_runtime.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

extern char **environ;

#define PCC_SUBPROCESS_TIMEOUT_RC (-124)
#define PCC_TIMEOUT_POLL_NS 10000000L
#define PCC_TIMEOUT_TERM_GRACE_MS 200

static void free_exec_argv(char **items, int64_t count) {
    if (items == NULL) return;
    for (int64_t i = 0; i < count; i++) free(items[i]);
    free(items);
}

static char **build_exec_argv(PyObject *argv, int64_t *count_out) {
    int64_t count = py_obj_len(argv);
    if (count <= 0) return NULL;
    char **items = (char **)calloc((size_t)count + 1, sizeof(char *));
    if (items == NULL) return NULL;

    for (int64_t i = 0; i < count; i++) {
        PyObject *index = py_int_from_i64(i);
        PyObject *item = py_obj_getitem(argv, index);
        py_decref(index);
        PyObject *text = py_obj_str(item);
        py_decref(item);
        if (text == NULL) {
            free_exec_argv(items, i);
            return NULL;
        }
        const char *raw = py_str_utf8(text);
        size_t size = raw != NULL ? strlen(raw) : 0;
        items[i] = (char *)malloc(size + 1);
        if (items[i] == NULL) {
            py_decref(text);
            free_exec_argv(items, i);
            return NULL;
        }
        if (size > 0) memcpy(items[i], raw, size);
        items[i][size] = '\0';
        py_decref(text);
    }
    items[count] = NULL;
    *count_out = count;
    return items;
}

static int64_t monotonic_millis(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (int64_t)now.tv_sec * 1000 + (int64_t)now.tv_nsec / 1000000;
}

static int64_t normalized_wait_status(int status) {
    if (WIFEXITED(status)) return (int64_t)WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return -(int64_t)WTERMSIG(status);
    return 127;
}

static int wait_for_exit(pid_t pid, int *status, int64_t deadline_ms) {
    struct timespec pause = {0, PCC_TIMEOUT_POLL_NS};
    for (;;) {
        pid_t waited = waitpid(pid, status, WNOHANG);
        if (waited == pid) return 1;
        if (waited < 0 && errno != EINTR) return -1;
        int64_t now_ms = monotonic_millis();
        if (now_ms < 0 || now_ms >= deadline_ms) return 0;
        nanosleep(&pause, NULL);
    }
}

static void terminate_process_group(pid_t pid, int *status) {
    /* POSIX_SPAWN_SETPGROUP made pid the process-group id. Keep the direct-pid
     * signal as a defensive fallback if a platform rejects group delivery. */
    if (kill(-pid, SIGTERM) != 0 && errno != ESRCH) kill(pid, SIGTERM);
    int64_t now_ms = monotonic_millis();
    int64_t deadline_ms = now_ms < 0 ? 0 : now_ms + PCC_TIMEOUT_TERM_GRACE_MS;
    int waited = wait_for_exit(pid, status, deadline_ms);
    if (waited == 1 || (waited < 0 && errno == ECHILD)) return;

    if (kill(-pid, SIGKILL) != 0 && errno != ESRCH) kill(pid, SIGKILL);
    while (waitpid(pid, status, 0) < 0 && errno == EINTR) {
    }
}

int64_t py_subprocess_run_timeout(
    PyObject *argv,
    int32_t capture_output,
    int64_t timeout_ms
) {
    if (timeout_ms <= 0) return 127;

    int64_t count = 0;
    char **items = build_exec_argv(argv, &count);
    if (items == NULL) return 127;

    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attr;
    int actions_ready = posix_spawn_file_actions_init(&actions) == 0;
    int attr_ready = posix_spawnattr_init(&attr) == 0;
    if (!actions_ready || !attr_ready) {
        if (actions_ready) posix_spawn_file_actions_destroy(&actions);
        if (attr_ready) posix_spawnattr_destroy(&attr);
        free_exec_argv(items, count);
        return 127;
    }

    int setup_error = 0;
    if (capture_output != 0) {
        setup_error = posix_spawn_file_actions_addopen(
            &actions, STDOUT_FILENO, "/dev/null", O_WRONLY, 0
        );
        if (setup_error == 0) {
            setup_error = posix_spawn_file_actions_addopen(
                &actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0
            );
        }
    }
    if (setup_error == 0) {
        setup_error = posix_spawnattr_setpgroup(&attr, 0);
    }
    if (setup_error == 0) {
        setup_error = posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    }

    pid_t pid = -1;
    int spawn_error = setup_error;
    if (spawn_error == 0) {
        spawn_error = posix_spawnp(
            &pid, items[0], &actions, &attr, items, environ
        );
    }
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    free_exec_argv(items, count);
    if (spawn_error != 0) return 127;

    int64_t start_ms = monotonic_millis();
    if (start_ms < 0) {
        int status = 0;
        terminate_process_group(pid, &status);
        return 127;
    }
    int status = 0;
    int waited = wait_for_exit(pid, &status, start_ms + timeout_ms);
    if (waited == 1) return normalized_wait_status(status);
    if (waited < 0) return 127;

    terminate_process_group(pid, &status);
    return PCC_SUBPROCESS_TIMEOUT_RC;
}
