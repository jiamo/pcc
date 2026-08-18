/* pcc/py_runtime/src/py_os_substrate.c
 *
 * C-only low-level OS primitives shared between the C and pcc-Python
 * runtime archives. Helpers here MUST live outside any module that
 * has a pcc-Python port in py/, because libpy_runtime_pcc_py.a swaps
 * those .o for the pcc-Python equivalents — anything pcc-Python
 * cannot express (struct stat layout, syscall buffer slabs, ...) has
 * to live here so both archives can link it.
 */
#include "py_internal.h"

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

PyObject *py_time_monotonic(void) {
    return py_float_from_f64((double)pcc_runtime_monotonic_us() / 1.0e6);
}

PyObject *py_time_perf_counter(void) {
    return py_float_from_f64((double)pcc_runtime_monotonic_us() / 1.0e6);
}

PyObject *py_time_time(void) {
    return py_float_from_f64((double)pcc_runtime_now_us() / 1.0e6);
}

PyObject *py_time_strftime(PyObject *fmt) {
    PyObject *fmt_str = py_obj_str(fmt);
    if (fmt_str == NULL) return NULL;
    const char *raw_fmt = py_str_utf8(fmt_str);
    if (raw_fmt == NULL) {
        py_decref(fmt_str);
        return py_str_new("", 0);
    }
    time_t now = (time_t)(pcc_runtime_now_us() / 1000000);
    struct tm tmv;
#if defined(_WIN32)
    struct tm *tmp = localtime(&now);
    if (tmp == NULL) {
        py_decref(fmt_str);
        return py_str_new("", 0);
    }
    tmv = *tmp;
#else
    if (localtime_r(&now, &tmv) == NULL) {
        py_decref(fmt_str);
        return py_str_new("", 0);
    }
#endif
    char buf[256];
    size_t n = strftime(buf, sizeof(buf), raw_fmt, &tmv);
    py_decref(fmt_str);
    return py_str_new(buf, (int64_t)n);
}

PyObject *py_sys_stdin_readline(void) {
    char *line = NULL;
    size_t cap = 0;
    ssize_t n = getline(&line, &cap, stdin);
    if (n < 0) {
        free(line);
        return py_str_new("", 0);
    }
    PyObject *out = py_str_new(line, (int64_t)n);
    free(line);
    return out;
}

PyObject *py_os_urandom(PyObject *n_obj) {
    int overflow = 0;
    int64_t n = py_int_to_i64(n_obj, &overflow);
    if (overflow || n < 0) {
        py_raise_owned(py_exc_new(PY_EXC_VALUEERROR, "negative argument not allowed"));
        return NULL;
    }
    PyObject *out = py_bytes_new(NULL, n);
    if (out == NULL || n == 0) return out;
    char *data = ((PyBytesObject *)out)->data;
#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__NetBSD__)
    arc4random_buf(data, (size_t)n);
    return out;
#else
    int fd = open("/dev/urandom", O_RDONLY);
    if (fd < 0) {
        py_decref(out);
        py_raise_owned(py_exc_new(PY_EXC_OSERROR, "could not open /dev/urandom"));
        return NULL;
    }
    int64_t pos = 0;
    while (pos < n) {
        ssize_t got = read(fd, data + pos, (size_t)(n - pos));
        if (got <= 0) {
            close(fd);
            py_decref(out);
            py_raise_owned(py_exc_new(PY_EXC_OSERROR, "could not read /dev/urandom"));
            return NULL;
        }
        pos += (int64_t)got;
    }
    close(fd);
    return out;
#endif
}

/* Classify a path by struct stat type:
 *   0 → stat() failed (missing or permission)
 *   1 → regular file
 *   2 → directory
 *   3 → other (symlink target, fifo, socket, ...)
 *
 * Used by py_os_path.{c,py} to implement isfile / isdir without
 * encoding the platform-specific struct stat layout in pcc-Python. */
int32_t py_path_stat_kind(const char *p) {
    struct stat st;
    if (p == (const char *)0) return 0;
    if (stat(p, &st) != 0) return 0;
    if (S_ISREG(st.st_mode)) return 1;
    if (S_ISDIR(st.st_mode)) return 2;
    return 3;
}

/* Return the path's last-modification time as IEEE-754 double seconds
 * since the Unix epoch — same shape as Python's os.path.getmtime
 * return value. NaN signals stat() failure (caller decides whether to
 * raise OSError or return NaN as-is). */
double py_path_stat_mtime(const char *p) {
    struct stat st;
    if (p == (const char *)0) return (double)0.0 / 0.0;
    if (stat(p, &st) != 0) return (double)0.0 / 0.0;
#if defined(__APPLE__)
    /* st_mtimespec is the canonical struct on Darwin. */
    return (double)st.st_mtimespec.tv_sec
         + (double)st.st_mtimespec.tv_nsec / 1.0e9;
#else
    return (double)st.st_mtim.tv_sec
         + (double)st.st_mtim.tv_nsec / 1.0e9;
#endif
}

int64_t py_path_stat_size(const char *p) {
    struct stat st;
    if (p == (const char *)0) return -1;
    if (stat(p, &st) != 0) return -1;
    return (int64_t)st.st_size;
}

/* Return the current working directory as a NUL-terminated cstring
 * pointer. Uses a thread-local static buffer so the caller does not
 * own the result — copy before another call clobbers it. NULL on
 * failure (e.g. cwd was deleted, ENOMEM). 8KB buffer accommodates
 * any reasonable POSIX path; longer paths fail rather than truncate. */
static __thread char _cwd_buf[8192];
const char *py_path_getcwd(void) {
    if (getcwd(_cwd_buf, sizeof(_cwd_buf)) == (const char *)0) {
        return (const char *)0;
    }
    return _cwd_buf;
}

/* Resolve PATH to a canonical absolute path (symlinks collapsed) via
 * realpath(3). Thread-local static buffer like py_path_getcwd: the caller
 * does not own the result and must copy before the next call. Returns NULL
 * when realpath(3) fails (e.g. a component does not exist, ENOENT), so the
 * caller can fall back to lexical normalization. 8KB exceeds PATH_MAX. */
static __thread char _realpath_buf[8192];
const char *py_path_realpath(const char *p) {
    if (p == (const char *)0) {
        return (const char *)0;
    }
    if (realpath(p, _realpath_buf) == (const char *)0) {
        return (const char *)0;
    }
    return _realpath_buf;
}

/* sys.platform value, picked at C compile time. Keeps the platform
 * string off CPython's runtime — matches Python's sys.platform format
 * (lower-case prefix without minor version). */
static const char *_sys_platform_cstr(void) {
#if defined(__APPLE__)
    return "darwin";
#elif defined(__linux__)
    return "linux";
#elif defined(_WIN32) || defined(_WIN64)
    return "win32";
#elif defined(__FreeBSD__)
    return "freebsd";
#else
    return "unknown";
#endif
}

static const char *_platform_machine_cstr(void) {
#if defined(__APPLE__) && defined(__aarch64__)
    return "arm64";
#elif defined(__aarch64__)
    return "aarch64";
#elif defined(_M_ARM64)
    return "ARM64";
#elif defined(__x86_64__) || defined(_M_X64)
    return "x86_64";
#elif defined(__i386__) || defined(_M_IX86)
    return "i386";
#elif defined(__arm__)
    return "arm";
#elif defined(__powerpc64__)
    return "ppc64";
#elif defined(__powerpc__)
    return "ppc";
#else
    return "";
#endif
}

PyObject *py_sys_platform_str(void) {
    const char *s = _sys_platform_cstr();
    int64_t n = 0;
    while (s[n] != '\0') n++;
    return py_str_new(s, n);
}

PyObject *py_platform_machine_str(void) {
    const char *s = _platform_machine_cstr();
    int64_t n = 0;
    while (s[n] != '\0') n++;
    return py_str_new(s, n);
}

PyObject *py_platform_release_str(void) {
    /* Only used by the fallback branch in
     * _host_target_triple_for_self_backend() when `cc -dumpmachine`
     * is unavailable. Keep this C-subset friendly; the normal self-host
     * path gets the precise Darwin/Linux triple from the compiler probe. */
    return py_str_new("0", 1);
}

/* Boxed `os.getcwd()` — same value as Python's os.getcwd. NULL on
 * error (matching CPython's OSError on stat failure; pcc's caller
 * gets a NULL it can null-check). */
PyObject *py_os_getcwd_str(void) {
    const char *p = py_path_getcwd();
    if (p == (const char *)0) return NULL;
    int64_t n = 0;
    while (p[n] != '\0') n++;
    return py_str_new(p, n);
}

/* `sys.path` — closed-world minimal, but preserve CPython's path[0]
 * contract for the owned entry modes.  Runtime package/import paths are
 * compiled into the closed-world artifact and are not published here. */
PyObject *py_sys_path_list(void) {
    PyObject *path0 = NULL;
    int32_t mode = py_program_mode();
    if (mode == 3 || mode == 4) {
        path0 = py_str_new("", 0);
    } else if (mode == 1) {
        const char *raw = py_program_argv(0);
        const char *resolved = raw != NULL ? py_path_realpath(raw) : NULL;
        if (resolved != NULL) {
            const char *slash = strrchr(resolved, '/');
            if (slash != NULL) {
                int64_t length = slash == resolved
                    ? 1
                    : (int64_t)(slash - resolved);
                path0 = py_str_new(resolved, length);
            }
        }
    }
    if (path0 == NULL) {
        path0 = py_os_getcwd_str();
    }
    if (path0 == NULL) {
        path0 = py_str_new("", 0);
    }
    PyObject *lst = py_list_new(0);
    py_list_append(lst, path0);
    py_decref(path0);
    return lst;
}

/* `os.access(path, mode)` — returns 1 if accessible, 0 otherwise.
 * `mode` is one of os.F_OK / R_OK / W_OK / X_OK (0 / 4 / 2 / 1).
 *
 * py_obj_str returns the same PyStr (with incref) when `path` is
 * already a string, so the helper does not need a type-check up
 * front; the unconditional incref/decref pair is cheap and keeps
 * the implementation portable across both archive variants. */
int32_t py_os_access(PyObject *path, int32_t mode) {
    if (path == NULL) return 0;
    PyObject *owned = py_obj_str(path);
    if (owned == NULL) return 0;
    const char *raw = py_str_utf8(owned);
    int ok = (raw != NULL && access(raw, (int)mode) == 0) ? 1 : 0;
    py_decref(owned);
    return ok;
}

int32_t py_os_write(int32_t fd, PyObject *data) {
    if (data == NULL) return -1;
    const char *ptr = NULL;
    int64_t len = 0;
    PyObject *curr = data;
    while (curr != NULL) {
        int32_t tag = py_type_of(curr);
        if (tag == PY_TYPE_STR) {
            ptr = py_str_utf8(curr);
            len = py_str_byte_len(curr);
            break;
        } else if (tag == PY_TYPE_BYTES) {
            ptr = ((PyBytesObject *)curr)->data;
            len = ((PyBytesObject *)curr)->byte_len;
            break;
        } else if (tag == PY_TYPE_BYTEARRAY) {
            ptr = ((PyByteArrayObject *)curr)->data;
            len = ((PyByteArrayObject *)curr)->byte_len;
            break;
        } else if (tag == PY_TYPE_MEMORYVIEW) {
            curr = pcc_gc_load_ptr(curr, &((PyMemoryViewObject *)curr)->base);
        } else {
            return -1;
        }
    }
    if (ptr == NULL || len < 0) return -1;
    ssize_t written = write((int)fd, ptr, (size_t)len);
    return (int32_t)written;
}
