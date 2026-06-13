/* C-only process helpers shared by all runtime archive variants.
 *
 * Keep these outside py_process.c: libpy_runtime_pcc_py.a replaces
 * py_process.o with the pcc-Python port, while this substrate object
 * remains available to both the C and pcc-Python runtime archives.
 */

#include "py_runtime.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int append_bytes(char **buf, int64_t *len, int64_t *cap,
                        const char *src, int64_t n) {
    if (n <= 0) return 0;
    if (*len + n + 1 > *cap) {
        int64_t new_cap = *cap > 0 ? *cap : 128;
        while (new_cap < *len + n + 1) new_cap *= 2;
        char *grown = (char *)realloc(*buf, (size_t)new_cap);
        if (grown == NULL) return -1;
        *buf = grown;
        *cap = new_cap;
    }
    memcpy(*buf + *len, src, (size_t)n);
    *len += n;
    (*buf)[*len] = '\0';
    return 0;
}

static int append_shell_quoted(char **buf, int64_t *len, int64_t *cap,
                               const char *src) {
    if (append_bytes(buf, len, cap, "'", 1) != 0) return -1;
    if (src != NULL) {
        for (int64_t i = 0; src[i] != '\0'; i++) {
            if (src[i] == '\'') {
                if (append_bytes(buf, len, cap, "'\\''", 4) != 0) return -1;
            } else {
                if (append_bytes(buf, len, cap, &src[i], 1) != 0) return -1;
            }
        }
    }
    return append_bytes(buf, len, cap, "'", 1);
}

static char *build_shell_command(PyObject *argv) {
    int64_t argc = py_obj_len(argv);
    if (argc <= 0) return NULL;
    char *cmd = NULL;
    int64_t len = 0;
    int64_t cap = 0;
    for (int64_t i = 0; i < argc; i++) {
        PyObject *idx = py_int_from_i64(i);
        PyObject *arg = py_obj_getitem(argv, idx);
        py_decref(idx);
        PyObject *arg_str = py_obj_str(arg);
        py_decref(arg);
        const char *raw = py_str_utf8(arg_str);
        if (i > 0 && append_bytes(&cmd, &len, &cap, " ", 1) != 0) {
            py_decref(arg_str);
            free(cmd);
            return NULL;
        }
        if (append_shell_quoted(&cmd, &len, &cap, raw) != 0) {
            py_decref(arg_str);
            free(cmd);
            return NULL;
        }
        py_decref(arg_str);
    }
    return cmd;
}

PyObject *py_subprocess_check_output(PyObject *argv) {
    char *cmd = build_shell_command(argv);
    if (cmd == NULL) return py_bytes_new("", 0);
    FILE *fp = popen(cmd, "r");
    free(cmd);
    if (fp == NULL) return py_bytes_new("", 0);

    char *out = NULL;
    int64_t len = 0;
    int64_t cap = 0;
    char tmp[4096];
    for (;;) {
        size_t n = fread(tmp, 1, sizeof(tmp), fp);
        if (n > 0) {
            if (append_bytes(&out, &len, &cap, tmp, (int64_t)n) != 0) {
                pclose(fp);
                free(out);
                return py_bytes_new("", 0);
            }
        }
        if (n < sizeof(tmp)) {
            break;
        }
    }
    int status = pclose(fp);
    if (status != 0) {
        py_raise(py_exc_new(PY_EXC_OSERROR, "subprocess failed"));
        free(out);
        return NULL;
    }
    PyObject *result = py_bytes_new(out != NULL ? out : "", len);
    free(out);
    return result;
}

int64_t py_subprocess_run(PyObject *argv, int32_t capture_output) {
    char *cmd = build_shell_command(argv);
    if (cmd == NULL) return 127;
    if (capture_output != 0) {
        int64_t len = (int64_t)strlen(cmd);
        int64_t cap = len + 1;
        if (append_bytes(&cmd, &len, &cap, " >/dev/null 2>&1", 16) != 0) {
            free(cmd);
            return 127;
        }
    }
    int rc = system(cmd);
    free(cmd);
    return (int64_t)rc;
}

PyObject *py_sys_executable_str(void) {
    const char *arg0 = py_program_argv(0);
    if (arg0 == NULL) return py_str_new("", 0);
    return py_str_new(arg0, (int64_t)strlen(arg0));
}

static PyObject *py_python_sys_attr_str(const char *attr) {
    const char *code =
        "import sys; print(getattr(sys, sys.argv[1], ''))";
    char *cmd = NULL;
    int64_t len = 0;
    int64_t cap = 0;
    int ok = 0;
    if (append_bytes(&cmd, &len, &cap, "python3 -c ", 11) == 0
        && append_shell_quoted(&cmd, &len, &cap, code) == 0
        && append_bytes(&cmd, &len, &cap, " ", 1) == 0
        && append_shell_quoted(&cmd, &len, &cap, attr) == 0) {
        ok = 1;
    }
    if (!ok) {
        free(cmd);
        return py_str_new("", 0);
    }

    FILE *fp = popen(cmd, "r");
    free(cmd);
    if (fp == NULL) return py_str_new("", 0);

    char *out = NULL;
    int64_t out_len = 0;
    int64_t out_cap = 0;
    char tmp[1024];
    for (;;) {
        size_t n = fread(tmp, 1, sizeof(tmp), fp);
        if (n > 0) {
            if (append_bytes(&out, &out_len, &out_cap, tmp, (int64_t)n) != 0) {
                pclose(fp);
                free(out);
                return py_str_new("", 0);
            }
        }
        if (n < sizeof(tmp)) break;
    }
    int rc = pclose(fp);
    while (out_len > 0
           && (out[out_len - 1] == '\n' || out[out_len - 1] == '\r')) {
        out_len--;
    }
    if (rc != 0 || out == NULL) {
        free(out);
        return py_str_new("", 0);
    }
    PyObject *result = py_str_new(out, out_len);
    free(out);
    return result;
}

PyObject *py_sys_prefix_str(int64_t kind) {
    return py_python_sys_attr_str(kind == 1 ? "base_prefix" : "prefix");
}

PyObject *py_sysconfig_get_config_var(PyObject *name) {
    PyObject *name_str = py_obj_str(name);
    if (name_str == NULL) return py_None;
    const char *key = py_str_utf8(name_str);
    if (key == NULL || key[0] == '\0') {
        py_decref(name_str);
        return py_None;
    }

    const char *code =
        "import sysconfig,sys; "
        "v=sysconfig.get_config_var(sys.argv[1]); "
        "print('' if v is None else v)";
    char *cmd = NULL;
    int64_t len = 0;
    int64_t cap = 0;
    int ok = 0;
    if (append_bytes(&cmd, &len, &cap, "python3 -c ", 11) == 0
        && append_shell_quoted(&cmd, &len, &cap, code) == 0
        && append_bytes(&cmd, &len, &cap, " ", 1) == 0
        && append_shell_quoted(&cmd, &len, &cap, key) == 0) {
        ok = 1;
    }
    py_decref(name_str);
    if (!ok) {
        free(cmd);
        return py_None;
    }

    FILE *fp = popen(cmd, "r");
    free(cmd);
    if (fp == NULL) return py_None;

    char *out = NULL;
    int64_t out_len = 0;
    int64_t out_cap = 0;
    char tmp[1024];
    for (;;) {
        size_t n = fread(tmp, 1, sizeof(tmp), fp);
        if (n > 0) {
            if (append_bytes(&out, &out_len, &out_cap, tmp, (int64_t)n) != 0) {
                pclose(fp);
                free(out);
                return py_None;
            }
        }
        if (n < sizeof(tmp)) break;
    }
    int rc = pclose(fp);
    while (out_len > 0
           && (out[out_len - 1] == '\n' || out[out_len - 1] == '\r')) {
        out_len--;
    }
    if (rc != 0 || out == NULL || out_len == 0) {
        free(out);
        return py_None;
    }
    PyObject *result = py_str_new(out, out_len);
    free(out);
    return result;
}

PyObject *py_os_listdir(PyObject *path) {
    PyObject *path_str = py_obj_str(path);
    if (path_str == NULL) return py_list_new(0);
    const char *raw = py_str_utf8(path_str);
    if (raw == NULL || raw[0] == '\0') {
        py_decref(path_str);
        return py_list_new(0);
    }

    char *cmd = NULL;
    int64_t len = 0;
    int64_t cap = 0;
    int ok = 0;
    if (append_bytes(&cmd, &len, &cap, "ls -1A -- ", 10) == 0
        && append_shell_quoted(&cmd, &len, &cap, raw) == 0) {
        ok = 1;
    }
    py_decref(path_str);
    if (!ok) {
        free(cmd);
        return py_list_new(0);
    }

    FILE *fp = popen(cmd, "r");
    free(cmd);
    if (fp == NULL) return py_list_new(0);

    PyObject *out = py_list_new(8);
    char *entry = NULL;
    int64_t entry_len = 0;
    int64_t entry_cap = 0;
    int ch;
    for (;;) {
        ch = fgetc(fp);
        if (ch == EOF) break;
        if (ch == '\n') {
            PyObject *item = py_str_new(entry != NULL ? entry : "", entry_len);
            py_list_append(out, item);
            py_decref(item);
            entry_len = 0;
            if (entry != NULL) entry[0] = '\0';
            continue;
        }
        {
            char c = (char)ch;
            if (append_bytes(&entry, &entry_len, &entry_cap, &c, 1) != 0) {
                break;
            }
        }
    }
    if (entry_len > 0) {
        PyObject *item = py_str_new(entry, entry_len);
        py_list_append(out, item);
        py_decref(item);
    }
    free(entry);
    (void)pclose(fp);
    return out;
}

PyObject *py_os_getpid(void) {
    return py_int_from_i64((int64_t)getpid());
}

static int has_path_separator(const char *s) {
    if (s == NULL) return 0;
    for (int64_t i = 0; s[i] != '\0'; i++) {
        if (s[i] == '/') return 1;
    }
    return 0;
}

static PyObject *which_direct(const char *cmd) {
    if (cmd == NULL || cmd[0] == '\0') return py_None;
    if (access(cmd, X_OK) != 0) return py_None;
    return py_str_new(cmd, (int64_t)strlen(cmd));
}

static int shell_is_space(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r'
        || c == '\f' || c == '\v';
}

PyObject *py_shlex_split(PyObject *text) {
    PyObject *text_str = py_obj_str(text);
    if (text_str == NULL) return py_list_new(0);
    const char *raw = py_str_utf8(text_str);
    if (raw == NULL) {
        py_decref(text_str);
        return py_list_new(0);
    }

    int64_t raw_len = (int64_t)strlen(raw);
    char *buf = (char *)malloc((size_t)raw_len + 1);
    PyObject *out = py_list_new(4);
    if (buf == NULL || out == NULL) {
        free(buf);
        py_decref(text_str);
        if (out != NULL) py_decref(out);
        return py_list_new(0);
    }

    int in_single = 0;
    int in_double = 0;
    int escaped = 0;
    int in_token = 0;
    int64_t n = 0;
    for (int64_t i = 0; i < raw_len; i++) {
        char c = raw[i];
        if (escaped) {
            buf[n++] = c;
            escaped = 0;
            in_token = 1;
            continue;
        }
        if (in_single) {
            if (c == '\'') {
                in_single = 0;
            } else {
                buf[n++] = c;
            }
            in_token = 1;
            continue;
        }
        if (in_double) {
            if (c == '"') {
                in_double = 0;
            } else if (c == '\\') {
                escaped = 1;
            } else {
                buf[n++] = c;
            }
            in_token = 1;
            continue;
        }
        if (shell_is_space(c)) {
            if (in_token) {
                PyObject *part = py_str_new(buf, n);
                py_list_append(out, part);
                py_decref(part);
                n = 0;
                in_token = 0;
            }
            continue;
        }
        if (c == '\'') {
            in_single = 1;
            in_token = 1;
            continue;
        }
        if (c == '"') {
            in_double = 1;
            in_token = 1;
            continue;
        }
        if (c == '\\') {
            escaped = 1;
            in_token = 1;
            continue;
        }
        buf[n++] = c;
        in_token = 1;
    }
    if (escaped) buf[n++] = '\\';
    if (in_token) {
        PyObject *part = py_str_new(buf, n);
        py_list_append(out, part);
        py_decref(part);
    }

    free(buf);
    py_decref(text_str);
    return out;
}

PyObject *py_shutil_which(PyObject *name) {
    PyObject *name_str = py_obj_str(name);
    if (name_str == NULL) return py_None;
    const char *cmd = py_str_utf8(name_str);
    if (cmd == NULL || cmd[0] == '\0') {
        py_decref(name_str);
        return py_None;
    }
    if (has_path_separator(cmd)) {
        PyObject *direct = which_direct(cmd);
        py_decref(name_str);
        return direct;
    }

    const char *path_env = getenv("PATH");
    if (path_env == NULL || path_env[0] == '\0') {
        py_decref(name_str);
        return py_None;
    }

    int64_t cmd_len = (int64_t)strlen(cmd);
    const char *seg = path_env;
    for (;;) {
        const char *end = seg;
        while (*end != '\0' && *end != ':') end++;
        int64_t dir_len = (int64_t)(end - seg);
        const char *dir = seg;
        const char dot[] = ".";
        if (dir_len == 0) {
            dir = dot;
            dir_len = 1;
        }
        int need_slash = (dir_len > 0 && dir[dir_len - 1] != '/') ? 1 : 0;
        int64_t total = dir_len + need_slash + cmd_len;
        char *candidate = (char *)malloc((size_t)total + 1);
        if (candidate == NULL) {
            py_decref(name_str);
            return py_None;
        }
        int64_t pos = 0;
        memcpy(candidate + pos, dir, (size_t)dir_len);
        pos += dir_len;
        if (need_slash) candidate[pos++] = '/';
        memcpy(candidate + pos, cmd, (size_t)cmd_len);
        pos += cmd_len;
        candidate[pos] = '\0';
        if (access(candidate, X_OK) == 0) {
            PyObject *out = py_str_new(candidate, pos);
            free(candidate);
            py_decref(name_str);
            return out;
        }
        free(candidate);
        if (*end == '\0') break;
        seg = end + 1;
    }

    py_decref(name_str);
    return py_None;
}

PyObject *py_tempdir_new(PyObject *prefix) {
    PyObject *prefix_str = py_obj_str(prefix);
    const char *prefix_raw = py_str_utf8(prefix_str);
    if (prefix_raw == NULL || prefix_raw[0] == '\0') {
        prefix_raw = "tmp";
    }
    const char *root = getenv("TMPDIR");
    if (root == NULL || root[0] == '\0') {
        root = "/tmp";
    }
    int64_t root_len = (int64_t)strlen(root);
    int64_t prefix_len = (int64_t)strlen(prefix_raw);
    int need_slash = (root_len > 0 && root[root_len - 1] != '/') ? 1 : 0;
    int64_t total = root_len + need_slash + prefix_len + 6;
    char *tmpl = (char *)malloc((size_t)total + 1);
    if (tmpl == NULL) {
        py_decref(prefix_str);
        return py_str_new("", 0);
    }
    int64_t pos = 0;
    memcpy(tmpl + pos, root, (size_t)root_len);
    pos += root_len;
    if (need_slash) tmpl[pos++] = '/';
    memcpy(tmpl + pos, prefix_raw, (size_t)prefix_len);
    pos += prefix_len;
    memcpy(tmpl + pos, "XXXXXX", 6);
    pos += 6;
    tmpl[pos] = '\0';

    char *made = mkdtemp(tmpl);
    PyObject *out = py_str_new(made != NULL ? made : "", made != NULL ? pos : 0);
    free(tmpl);
    py_decref(prefix_str);
    return out;
}

void py_tempdir_cleanup(PyObject *path) {
    PyObject *path_str = py_obj_str(path);
    const char *raw = py_str_utf8(path_str);
    if (raw == NULL || raw[0] == '\0') {
        py_decref(path_str);
        return;
    }
    char *cmd = NULL;
    int64_t len = 0;
    int64_t cap = 0;
    if (
        append_bytes(&cmd, &len, &cap, "rm -rf ", 7) == 0
        && append_shell_quoted(&cmd, &len, &cap, raw) == 0
    ) {
        (void)system(cmd);
    }
    free(cmd);
    py_decref(path_str);
}
