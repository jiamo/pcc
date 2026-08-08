"""pcc-Python: line-level LCS diff core (inlined into the compare app).

Pure logic, no UI.  Compares two arrays of line ids (integers) and emits an
edit script.  The DP table is int32, allocated at init via calloc; the
pointer lives in a named i64 global (verified pattern: global_addr + offset).

Owned ABI:

  pcc_gui_diff_init(max_lines) -> i32
  pcc_gui_diff_compute(left, l_n, right, r_n, ops, ops_cap) -> i32
      left/right: i64 arrays of line ids
      ops: 3*i64 records {type, left_line, right_line}
      type: 0=equal 1=delete(left-only) 2=insert(right-only)
      returns op count; negative = error
"""

from pcc.unsafe import (
    calloc,
    define_global_i64_array,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    ptr_is_null,
    ptr_to_int,
    store_i32,
    store_i64,
)

# state: dp_ptr@0, max_lines@8
define_global_i64_array("pcc_gui_diff_state", 0, 0)

_OPS_EQUAL = 0
_OPS_DELETE = 1
_OPS_INSERT = 2
_OPS_CHANGE = 3   # left+right both present, different content (BC "modified")


def _coalesce(ops, n: int) -> int:
    """Pair each maximal delete-run with the insert-run that follows it,
    turning aligned del/ins into CHANGE rows (Beyond-Compare style).  Extra
    deletes/inserts stay as-is.  Compacts in place: writes never overtake the
    read cursor, since each run emits max(nd,ni) <= nd+ni records."""
    w: int = 0
    r: int = 0
    while r < n:
        t: int = load_i64(ops, r * 24 + 0)
        if t == _OPS_DELETE:
            ds: int = r
            while r < n and load_i64(ops, r * 24 + 0) == _OPS_DELETE:
                r += 1
            de: int = r
            iss: int = r
            while r < n and load_i64(ops, r * 24 + 0) == _OPS_INSERT:
                r += 1
            nd: int = de - ds
            ni: int = r - iss
            npair: int = nd
            if ni < npair:
                npair = ni
            p: int = 0
            while p < npair:
                store_i64(ops, w * 24 + 0, _OPS_CHANGE)
                store_i64(ops, w * 24 + 8, load_i64(ops, (ds + p) * 24 + 8))
                store_i64(ops, w * 24 + 16, load_i64(ops, (iss + p) * 24 + 16))
                w += 1
                p += 1
            while p < nd:
                store_i64(ops, w * 24 + 0, _OPS_DELETE)
                store_i64(ops, w * 24 + 8, load_i64(ops, (ds + p) * 24 + 8))
                store_i64(ops, w * 24 + 16, -1)
                w += 1
                p += 1
            q: int = npair
            while q < ni:
                store_i64(ops, w * 24 + 0, _OPS_INSERT)
                store_i64(ops, w * 24 + 8, -1)
                store_i64(ops, w * 24 + 16, load_i64(ops, (iss + q) * 24 + 16))
                w += 1
                q += 1
        else:
            store_i64(ops, w * 24 + 0, t)
            store_i64(ops, w * 24 + 8, load_i64(ops, r * 24 + 8))
            store_i64(ops, w * 24 + 16, load_i64(ops, r * 24 + 16))
            w += 1
            r += 1
    return w


def _g(off: int) -> int:
    return load_i64(global_addr("pcc_gui_diff_state"), off)


def _setg(off: int, value: int) -> None:
    store_i64(global_addr("pcc_gui_diff_state"), off, value)


def pcc_gui_diff_init(max_lines: int) -> int:
    if max_lines <= 0 or max_lines > 4096:
        return -1
    n: int = (max_lines + 1) * (max_lines + 1) * 4
    dp = calloc(n, 1)
    if ptr_is_null(dp):
        return -2
    _setg(0, ptr_to_int(dp))
    _setg(8, max_lines)
    return 0


def pcc_gui_diff_compute(left, l_n: int, right, r_n: int, ops, ops_cap: int) -> int:
    if l_n <= 0 or r_n <= 0:
        return -3
    if l_n > _g(8) or r_n > _g(8):
        return -4
    dp = int_to_ptr(_g(0))
    m: int = r_n + 1
    # LCS DP: dp[i*(m) + j]
    i: int = 1
    while i <= l_n:
        j: int = 1
        while j <= r_n:
            a: int = load_i64(left, (i - 1) * 8)
            b: int = load_i64(right, (j - 1) * 8)
            if a == b:
                store_i32(dp, (i * m + j) * 4,
                          load_i32(dp, ((i - 1) * m + (j - 1)) * 4) + 1)
            else:
                up: int = load_i32(dp, ((i - 1) * m + j) * 4)
                lf: int = load_i32(dp, (i * m + (j - 1)) * 4)
                if up > lf:
                    store_i32(dp, (i * m + j) * 4, up)
                else:
                    store_i32(dp, (i * m + j) * 4, lf)
            j += 1
        i += 1
    # backtrace
    k: int = 0
    ii: int = l_n
    jj: int = r_n
    while ii > 0 or jj > 0:
        if k >= ops_cap:
            return -5
        if ii > 0 and jj > 0 and load_i64(left, (ii - 1) * 8) == load_i64(right, (jj - 1) * 8):
            store_i64(ops, k * 24 + 0, _OPS_EQUAL)
            store_i64(ops, k * 24 + 8, ii - 1)
            store_i64(ops, k * 24 + 16, jj - 1)
            ii -= 1
            jj -= 1
        elif jj > 0 and (
            ii == 0 or
            load_i32(dp, ((ii - 1) * m + jj) * 4) <= load_i32(dp, (ii * m + (jj - 1)) * 4)
        ):
            store_i64(ops, k * 24 + 0, _OPS_INSERT)
            store_i64(ops, k * 24 + 8, -1)
            store_i64(ops, k * 24 + 16, jj - 1)
            jj -= 1
        else:
            store_i64(ops, k * 24 + 0, _OPS_DELETE)
            store_i64(ops, k * 24 + 8, ii - 1)
            store_i64(ops, k * 24 + 16, -1)
            ii -= 1
        k += 1
    # reverse ops in place (backtrace emits reversed)
    lo: int = 0
    hi: int = k - 1
    while lo < hi:
        t0: int = load_i64(ops, lo * 24 + 0)
        l0: int = load_i64(ops, lo * 24 + 8)
        r0: int = load_i64(ops, lo * 24 + 16)
        store_i64(ops, lo * 24 + 0, load_i64(ops, hi * 24 + 0))
        store_i64(ops, lo * 24 + 8, load_i64(ops, hi * 24 + 8))
        store_i64(ops, lo * 24 + 16, load_i64(ops, hi * 24 + 16))
        store_i64(ops, hi * 24 + 0, t0)
        store_i64(ops, hi * 24 + 8, l0)
        store_i64(ops, hi * 24 + 16, r0)
        lo += 1
        hi -= 1
    return k
