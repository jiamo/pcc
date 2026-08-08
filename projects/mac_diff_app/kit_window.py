"""gui_example2 — dual-pane file compare (Meld diff core + BC-style UI).

Opens two REAL files, splits into lines, hashes each line, diffs with the
LCS core, and renders two panes with per-line color blocks plus REAL TEXT
(CATextLayer glyphs), a difference overview bar, a status bar, a toolbar,
and a slow highlight walk.

Compiled with pcc1 (self backend, no libpython).  Run: cd gui_example2 && ./run.sh
"""

from pcc.extern import c_ptr, c_int64, c_int32, extern
from pcc.unsafe import (
    call_i64_ptr2, call_i64_ptr3, call_i64_ptr3_i64_i64_i64, calloc, cstr,
    define_global_i64_array,
    global_addr, int_to_ptr, load_i64, load_i8, ptr_add, ptr_is_null,
    ptr_to_int, stack_alloc, store_i64, store_i8, wrapping_mul_i64,
)

# e2 state: win@0 textfn@8 (i64 slots — native pointers must NOT be
# module-level Python variables, GC would pin them)
define_global_i64_array("e2_state", 0, 0, 0)
import pcc_gui_high as gui
import pcc_gui_kit as kit
from diff_core import pcc_gui_diff_init, pcc_gui_diff_compute

py_program_argc_fn = extern("py_program_argc", (), c_int32)
py_program_argv_fn = extern("py_program_argv", (c_int64,), c_ptr)
open_fn = extern("open", (c_ptr, c_int32), c_int64)
read_fn = extern("read", (c_int64, c_ptr, c_int64), c_int64)
close_fn = extern("close", (c_int64,), c_int64)

IDS_L = calloc(16384, 1)
IDS_R = calloc(16384, 1)
BUF_L = calloc(65536, 1)
BUF_R = calloc(65536, 1)
LINES_L = calloc(32768, 1)
LINES_R = calloc(32768, 1)
PATH_L = calloc(1024, 1)
PATH_R = calloc(1024, 1)
USED = calloc(512, 1)
OPLINE = calloc(2048, 1)
RED = calloc(24, 1)
PANE_L = calloc(65536, 1)
PANE_R = calloc(65536, 1)
SPEC_L = calloc(16384, 1)
SPEC_R = calloc(16384, 1)
pane_nlines = 0
pane_lplen = 0
pane_rplen = 0


def read_lines(path, buf, ids, lines) -> int:
    fd = open_fn(path, 0)
    if fd < 0:
        return -1
    total = read_fn(fd, buf, 65536)
    close_fn(fd)
    if total <= 0:
        return 0
    count = 0
    start = 0
    h = -7046029254386353131  # signed 64-bit hash init (u64 literal differs by backend)
    i = 0
    while i < total:
        b = load_i8(buf, i) & 0xFF
        if b == 10:
            if count < 2048:
                store_i64(lines, count * 16, start)
                store_i64(lines, count * 16 + 8, i - start)
                store_i64(ids, count * 8, h)
            count = count + 1
            start = i + 1
            h = -7046029254386353131
        else:
            h = wrapping_mul_i64(h, 31) + b
        i = i + 1
    if start < total:
        if count < 2048:
            store_i64(lines, count * 16, start)
            store_i64(lines, count * 16 + 8, total - start)
            store_i64(ids, count * 8, h)
        count = count + 1
    return count


def _strcpy(dst, src) -> None:
    i = 0
    b = load_i8(src, 0) & 0xFF
    while b != 0:
        store_i8(dst, i, b)
        i = i + 1
        b = load_i8(src, i) & 0xFF
    store_i8(dst, i, 0)


def _common_prefix(pa, la: int, pb, lb: int) -> int:
    m = la
    if lb < m:
        m = lb
    i = 0
    while i < m and (load_i8(pa, i) & 0xFF) == (load_i8(pb, i) & 0xFF):
        i = i + 1
    while i > 0 and i < la and (load_i8(pa, i) & 0xC0) == 0x80:
        i = i - 1
    return i


def _common_suffix(pa, la: int, pb, lb: int, cp: int) -> int:
    maxa = la - cp
    maxb = lb - cp
    m = maxa
    if maxb < m:
        m = maxb
    i = 0
    while i < m and (load_i8(pa, la - 1 - i) & 0xFF) == (load_i8(pb, lb - 1 - i) & 0xFF):
        i = i + 1
    while i > 0 and (load_i8(pa, la - i) & 0xC0) == 0x80:
        i = i - 1
    return i


def _calc_red(opp) -> None:
    """RED[cp, lred, rred]: byte offset + per-side length of the differing span
    of a CHANGE row (common prefix/suffix stripped).  Pure pcc-Python."""
    ll = load_i64(opp, 8)
    rl = load_i64(opp, 16)
    lpa = ptr_add(BUF_L, load_i64(LINES_L, ll * 16))
    lpl = load_i64(LINES_L, ll * 16 + 8)
    rpa = ptr_add(BUF_R, load_i64(LINES_R, rl * 16))
    rpl = load_i64(LINES_R, rl * 16 + 8)
    cp = _common_prefix(lpa, lpl, rpa, rpl)
    cs = _common_suffix(lpa, lpl, rpa, rpl, cp)
    lred = lpl - cp - cs
    rred = rpl - cp - cs
    if lred < 0:
        lred = 0
    if rred < 0:
        rred = 0
    store_i64(RED, 0, cp)
    store_i64(RED, 8, lred)
    store_i64(RED, 16, rred)


def _sim(ll: int, rl: int) -> int:
    pa = ptr_add(BUF_L, load_i64(LINES_L, ll * 16))
    plen = load_i64(LINES_L, ll * 16 + 8)
    pb = ptr_add(BUF_R, load_i64(LINES_R, rl * 16))
    qlen = load_i64(LINES_R, rl * 16 + 8)
    cp = _common_prefix(pa, plen, pb, qlen)
    cs = _common_suffix(pa, plen, pb, qlen, cp)
    return cp + cs


def _wnum(pane, pos: int, num: int) -> int:
    tmp = stack_alloc(8)
    d = itoa(num, tmp)
    pad = 4 - d
    j = 0
    while j < pad:
        store_i8(pane, pos, 32)
        pos = pos + 1
        j = j + 1
    j = 0
    while j < d:
        store_i8(pane, pos, load_i8(tmp, j))
        pos = pos + 1
        j = j + 1
    store_i8(pane, pos, 32)
    store_i8(pane, pos + 1, 32)
    return pos + 2


def _wcopy(pane, pos: int, buf, off: int, ln: int) -> int:
    j = 0
    while j < ln:
        store_i8(pane, pos, load_i8(buf, off + j))
        pos = pos + 1
        j = j + 1
    return pos


def _pane_line(pane, pos: int, spec, k: int, tbl, buf, lineno0: int, kind: int, redcp: int, redlen: int) -> int:
    ls = pos
    off = load_i64(tbl, lineno0 * 16)
    ln = load_i64(tbl, lineno0 * 16 + 8)
    if ln > 300:
        ln = 300
    pos = _wcopy(pane, pos, buf, off, ln)
    store_i8(pane, pos, 10)
    rst = 0
    rln = 0
    if kind == 1 and redlen > 0:
        rst = ls + redcp
        rln = redlen
    store_i64(spec, k * 48 + 0, ls)
    store_i64(spec, k * 48 + 8, pos - ls)
    store_i64(spec, k * 48 + 16, kind)
    store_i64(spec, k * 48 + 24, rst)
    store_i64(spec, k * 48 + 32, rln)
    store_i64(spec, k * 48 + 40, lineno0 + 1)
    return pos + 1


def _pane_gap(pane, pos: int, spec, k: int) -> int:
    store_i8(pane, pos, 10)
    store_i64(spec, k * 48 + 0, pos)
    store_i64(spec, k * 48 + 8, 0)
    store_i64(spec, k * 48 + 16, 2)
    store_i64(spec, k * 48 + 24, 0)
    store_i64(spec, k * 48 + 32, 0)
    store_i64(spec, k * 48 + 40, 0)
    return pos + 1


def _build_panes() -> None:
    global pane_nlines
    global pane_lplen
    global pane_rplen
    lp = 0
    rp = 0
    k = 0
    i = 0
    while i < n:
        t = load_i64(ops, i * 24)
        if mode == 1 and t == 0:
            store_i64(OPLINE, i * 8, -1)
            i = i + 1
            continue
        store_i64(OPLINE, i * 8, k)
        ll = load_i64(ops, i * 24 + 8)
        rl = load_i64(ops, i * 24 + 16)
        lcp = 0
        llen = 0
        rlen = 0
        if t == 3:
            _calc_red(ptr_add(ops, i * 24))
            lcp = load_i64(RED, 0)
            llen = load_i64(RED, 8)
            rlen = load_i64(RED, 16)
        lk = 2
        if ll >= 0:
            lk = 0 if t == 0 else 1
            lp = _pane_line(PANE_L, lp, SPEC_L, k, LINES_L, BUF_L, ll, lk, lcp, llen)
        else:
            lp = _pane_gap(PANE_L, lp, SPEC_L, k)
        if rl >= 0:
            rk = 0 if t == 0 else 1
            rp = _pane_line(PANE_R, rp, SPEC_R, k, LINES_R, BUF_R, rl, rk, lcp, rlen)
        else:
            rp = _pane_gap(PANE_R, rp, SPEC_R, k)
        k = k + 1
        i = i + 1
    pane_nlines = k
    pane_lplen = lp
    pane_rplen = rp


def _repair_ops(ops, n: int) -> int:
    """Re-pair each delete-run with the following insert-run by byte
    similarity (BC-style): most-similar lines become CHANGE rows, the rest
    stay as left/right orphans.  In-place compaction (output <= input)."""
    w = 0
    r = 0
    while r < n:
        t = load_i64(ops, r * 24)
        if t == 1:
            ds = r
            while r < n and load_i64(ops, r * 24) == 1:
                r = r + 1
            de = r
            iss = r
            while r < n and load_i64(ops, r * 24) == 2:
                r = r + 1
            nd = de - ds
            ni = r - iss
            z = 0
            while z < ni and z < 512:
                store_i8(USED, z, 0)
                z = z + 1
            p = 0
            while p < nd:
                ll = load_i64(ops, (ds + p) * 24 + 8)
                best = -1
                bestsim = 0
                q = 0
                while q < ni:
                    if q < 512 and load_i8(USED, q) == 0:
                        rl = load_i64(ops, (iss + q) * 24 + 16)
                        sm = _sim(ll, rl)
                        mn = load_i64(LINES_L, ll * 16 + 8)
                        qlen = load_i64(LINES_R, rl * 16 + 8)
                        if qlen < mn:
                            mn = qlen
                        thr = mn // 3
                        if thr < 2:
                            thr = 2
                        if sm >= thr and sm > bestsim:
                            bestsim = sm
                            best = q
                    q = q + 1
                if best >= 0:
                    store_i8(USED, best, 1)
                    rl = load_i64(ops, (iss + best) * 24 + 16)
                    store_i64(ops, w * 24 + 0, 3)
                    store_i64(ops, w * 24 + 8, ll)
                    store_i64(ops, w * 24 + 16, rl)
                    w = w + 1
                else:
                    store_i64(ops, w * 24 + 0, 1)
                    store_i64(ops, w * 24 + 8, ll)
                    store_i64(ops, w * 24 + 16, -1)
                    w = w + 1
                p = p + 1
            q = 0
            while q < ni:
                if q >= 512 or load_i8(USED, q) == 0:
                    rl = load_i64(ops, (iss + q) * 24 + 16)
                    store_i64(ops, w * 24 + 0, 2)
                    store_i64(ops, w * 24 + 8, -1)
                    store_i64(ops, w * 24 + 16, rl)
                    w = w + 1
                q = q + 1
        else:
            store_i64(ops, w * 24 + 0, t)
            store_i64(ops, w * 24 + 8, load_i64(ops, r * 24 + 8))
            store_i64(ops, w * 24 + 16, load_i64(ops, r * 24 + 16))
            w = w + 1
            r = r + 1
    return w


gui.init(cstr("pcc diff"), 900, 600,
         cstr("/Users/jiamo/my/pcc/projects/mac_diff_app/libpcc_gui_metal.dylib"))

gui.theme(0, 0xFFE8E8E8)   # header bar
gui.theme(1, 0xFFF0F0F0)   # pane background
gui.theme(2, 0xFFFBE3E4)   # difference band (light pink, BC-like)
gui.theme(3, 0xFFFBE3E4)   # difference band
gui.theme(4, 0xFFFBE3E4)   # difference band
gui.theme(5, 0xFFD8D8D8)   # status bar

def _open_files() -> None:
    global left_n
    global right_n
    lp = cstr("/Users/jiamo/my/pcc/projects/mac_diff_app/samples/left.txt")
    rp = cstr("/Users/jiamo/my/pcc/projects/mac_diff_app/samples/right.txt")
    if py_program_argc_fn() >= 3:
        lp = py_program_argv_fn(1)
        rp = py_program_argv_fn(2)
    _strcpy(PATH_L, lp)
    _strcpy(PATH_R, rp)
    left_n = read_lines(PATH_L, BUF_L, IDS_L, LINES_L)
    right_n = read_lines(PATH_R, BUF_R, IDS_R, LINES_R)


_open_files()

pcc_gui_diff_init(256)
ops = stack_alloc(6144)
n = pcc_gui_diff_compute(IDS_L, left_n, IDS_R, right_n, ops, 256)
n = _repair_ops(ops, n)

n_eq = 0
n_del = 0
n_ins = 0
n_chg = 0
i = 0
while i < n:
    t = load_i64(ops, i * 24 + 0)
    if t == 0:
        n_eq = n_eq + 1
    elif t == 1:
        n_del = n_del + 1
    elif t == 2:
        n_ins = n_ins + 1
    else:
        n_chg = n_chg + 1
    i = i + 1

# structured smoke output — the verification truth (not just exit code)
print("PCC_MAC_DIFF_SMOKE left_rows=", left_n,
      " right_rows=", right_n,
      " ops=", n,
      " equal=", n_eq,
      " deleted=", n_del,
      " inserted=", n_ins,
      " changed=", n_chg)

mode = 0
status_msg = 0
dirty = 1
last_w = 0
last_h = 0
click_x = stack_alloc(8)
click_y = stack_alloc(8)
cur = 0
slow = 0
_slot = 0
frame = 0
offset = 0          # top visible left-row (scroll)
prev_drawn = 0      # screen rows drawn last frame (to hide the rest)
g_right_x = 436     # right pane x (updated per frame)
g_nav_x = 886
g_status_y = 566
g_max_rows = 13
VISIBLE = 23        # rows that fit under the panes


def op_left_row(op_idx: int) -> int:
    """Unified visible-row position at op_idx (for scroll/jump)."""
    r = 0
    i = 0
    while i < op_idx and i < n:
        tt = load_i64(ops, i * 24 + 0)
        if not (mode == 1 and tt == 0):
            r = r + 1
        i = i + 1
    return r


def itoa(v: int, buf) -> int:
    """Decimal ASCII of a non-negative int; returns byte length."""
    tmp = stack_alloc(16)
    i = 0
    if v == 0:
        store_i8(buf, 0, 48)
        return 1
    while v > 0:
        store_i8(tmp, i, 48 + (v % 10))
        v = v // 10
        i = i + 1
    k = 0
    while k < i:
        store_i8(buf, k, load_i8(tmp, i - 1 - k))
        k = k + 1
    return i


def _emit_text(slot: int, x: int, y: int, text, ln: int, font: int, color: int) -> None:
    gui.text(slot, x, y, text, ln, font, color)


def _hide_cell(base: int, row: int) -> None:
    d = stack_alloc(1)
    gui.text(base + row * 2, 0, 0, d, 0, 11, 0)
    gui.text(base + row * 2 + 1, 0, 0, d, 0, 11, 0)


def _hide_rows(from_row: int, to_row: int) -> None:
    d = stack_alloc(1)
    r = from_row
    while r < to_row:
        gui.text(20 + r * 2, 0, 0, d, 0, 11, 0)
        gui.text(20 + r * 2 + 1, 0, 0, d, 0, 11, 0)
        gui.text(260 + r * 2, 0, 0, d, 0, 11, 0)
        gui.text(260 + r * 2 + 1, 0, 0, d, 0, 11, 0)
        r = r + 1


def _line_text(side: int, op_ptr, x: int, y: int, color: int, row: int, hstart: int, hlen: int) -> None:
    """Draw the line number + real text.  Text layers are keyed by on-screen
    ROW (left 20+, right 260+), so a scrolled-off row's layer is reused by
    whatever now occupies that row and undrawn rows are hidden — no ghosts,
    no fixed per-file-line slot collisions."""
    line_no = load_i64(op_ptr, 8 if side == 0 else 16) + 1
    base = 20 if side == 0 else 260
    nbuf = stack_alloc(8)
    nlen = itoa(line_no, nbuf)
    _emit_text(base + row * 2, 14 if side == 0 else g_right_x + 6, y,
               nbuf, nlen, 11, 0xFF999999)
    buf = BUF_L if side == 0 else BUF_R
    tbl = LINES_L if side == 0 else LINES_R
    off = load_i64(tbl, line_no * 16 - 16)
    ln = load_i64(tbl, line_no * 16 - 8)
    if ln > 300:
        ln = 300
    gui.text_hl(base + row * 2 + 1, x, y, ptr_add(buf, off), ln, 13, color, hstart, hlen)


# --- composition-tree layout: the kernel does measure/arrange, the app
# only declares nodes + intrinsic sizes and reads back computed rects.
# node ids are deterministic (init resets count): root0 tb1 body2 st3
# lm4 lp5 dv6 rp7 rm8
kit.pcc_kit_init(16)
kit.pcc_kit_create(-1)      # 0 root  (stack-v default)
kit.pcc_kit_create(0)       # 1 toolbar
kit.pcc_kit_create(0)       # 2 body
kit.pcc_kit_layout(2, 1)    # body = stack-h
kit.pcc_kit_create(0)       # 3 status
kit.pcc_kit_create(2)       # 4 left margin
kit.pcc_kit_create(2)       # 5 left pane
kit.pcc_kit_create(2)       # 6 divider
kit.pcc_kit_create(2)       # 7 right pane
kit.pcc_kit_create(2)       # 8 right margin
kit.pcc_kit_rect(4, 0, 0, 8, 0, 0xFFFFFFFF)   # left margin 8px
kit.pcc_kit_rect(6, 0, 0, 8, 0, 0xFFCCCCCC)   # divider gap 8px
kit.pcc_kit_rect(8, 0, 0, 4, 0, 0xFFFFFFFF)   # right margin 4px


while gui.running():
    if gui.poll_click(click_x, click_y) != 0:
        dirty = 1
        cx = load_i64(click_x, 0)
        cy = load_i64(click_y, 0)
        if cy >= 8 and cy < 38:
            if cx >= 16 and cx < 106:
                status_msg = 2
                p1 = stack_alloc(512)
                p2 = stack_alloc(512)
                if gui.open_panel2(p1, p2) == 0:
                    _strcpy(PATH_L, p1)
                    _strcpy(PATH_R, p2)
                    left_n = read_lines(PATH_L, BUF_L, IDS_L, LINES_L)
                    right_n = read_lines(PATH_R, BUF_R, IDS_R, LINES_R)
                    n = pcc_gui_diff_compute(IDS_L, left_n, IDS_R, right_n, ops, 256)
                    n = _repair_ops(ops, n)
                    cur = 0
                    offset = 0
            elif cx >= 116 and cx < 226:
                mode = 1 - mode
                status_msg = 0
            elif cx >= 240 and cx < 320:
                # Previous difference
                j = cur - 1
                steps = 0
                while steps < n and j >= 0:
                    if j < 0:
                        j = n - 1
                    if load_i64(ops, j * 24 + 0) != 0:
                        break
                    j = j - 1
                    steps = steps + 1
                if j >= 0:
                    cur = j
                    fl = load_i64(OPLINE, cur * 8)
                    if fl >= 0:
                        gui.pane_focus(0, fl)
                        gui.pane_focus(1, fl)
            elif cx >= 320 and cx < 400:
                # Next difference
                j = cur + 1
                steps = 0
                while steps < n:
                    if j >= n:
                        j = 0
                    if load_i64(ops, j * 24 + 0) != 0:
                        break
                    j = j + 1
                    steps = steps + 1
                if j < n:
                    cur = j
                    fl = load_i64(OPLINE, cur * 8)
                    if fl >= 0:
                        gui.pane_focus(0, fl)
                        gui.pane_focus(1, fl)
            elif cx >= g_nav_x and cx < g_nav_x + 8 and cy >= 48 and cy < g_status_y:
                # overview bar click -> nearest diff op -> focus panes
                span = g_status_y - 48
                target = ((cy - 48) * n) // span
                best = -1
                bestd = 0
                oi = 0
                while oi < n:
                    if load_i64(ops, oi * 24 + 0) != 0:
                        d = oi - target
                        if d < 0:
                            d = -d
                        if best < 0 or d < bestd:
                            bestd = d
                            best = oi
                    oi = oi + 1
                if best >= 0:
                    cur = best
                    fl = load_i64(OPLINE, cur * 8)
                    if fl >= 0:
                        gui.pane_focus(0, fl)
                        gui.pane_focus(1, fl)
                    status_msg = 3

    _slot = 0
    wbuf = stack_alloc(8)
    hbuf = stack_alloc(8)
    gui.window_size(wbuf, hbuf)
    w = load_i64(wbuf, 0)
    h = load_i64(hbuf, 0)
    if w < 300:
        w = 300
    if h < 300:
        h = 300
    # startup warm-up: keep painting until the window is shown and the
    # Metal drawable is settled, else the first frame lands before the
    # window is visible and we'd sit on a black layer until a click.
    if frame < 60:
        dirty = 1
    # after that: only repaint on change -> the poll loop stays hot, so
    # clicks (e.g. Open) respond immediately instead of lagging.
    if w != last_w or h != last_h:
        dirty = 1
        last_w = w
        last_h = h
    if dirty:
        gui.resize(w, h)
        left_w = (w - 20) // 2
        body_h = h - 34 - 48
        if body_h < 1:
            body_h = 1
        kit._s8(1, 48, w)
        kit._s8(1, 56, 48)
        kit._s8(2, 48, w)
        kit._s8(2, 56, body_h)
        kit._s8(3, 48, w)
        kit._s8(3, 56, 34)
        kit._s8(5, 48, left_w)
        kit._s8(5, 56, body_h)
        kit._s8(7, 48, left_w)
        kit._s8(7, 56, body_h)
        kit.pcc_kit_layout_tree(0, w, h)
        lp_x = kit._n8(5, 32)
        lp_y = kit._n8(5, 40)
        lp_w = kit._n8(5, 48)
        lp_h = kit._n8(5, 56)
        rp_x = kit._n8(7, 32)
        rp_w = kit._n8(7, 48)
        right_x = rp_x
        g_right_x = right_x
        g_nav_x = nav_x
        g_status_y = status_y
        g_max_rows = max_rows
        nav_x = w - 14
        status_y = h - 34
        visible = (h - 48 - 34) // 20
        if visible < 1:
            visible = 1
        if visible > 119:
            visible = 119
        gui.clear(0xFFFFFFFF)
        _slot = 1
        tb = 0xFF4A4A4A
        gui.button(16, 8, 90, 30, 0xFFF5F5F5)
        gui.button(116, 8, 110, 30, 0xFFF5F5F5)
        gui.button(240, 8, 80, 30, 0xFFF5F5F5)
        gui.button(320, 8, 80, 30, 0xFFF5F5F5)
        # real button labels (CATextLayer)
        gui.text(1, 16 + (90 - 7 * 4) // 2, 16, cstr("Open"), 4, 13, 0xFF333333)
        gui.text(3, 116 + (110 - 7 * 5) // 2, 16, cstr("Diffs"), 5, 13, 0xFF333333)
        gui.text(4, 240 + (80 - 7 * 4) // 2, 16, cstr("Prev"), 4, 13, 0xFF333333)
        gui.text(5, 320 + (80 - 7 * 4) // 2, 16, cstr("Next"), 4, 13, 0xFF333333)
        _slot = 6
        gui.rect(8, 36, 420, 4, 0xFF666666)
        gui.rect(right_x, 36, left_w, 4, 0xFFCCCCCC)

        _build_panes()
        gui.pane_set(0, lp_x, lp_y, lp_w, lp_h, PANE_L, pane_lplen, SPEC_L, pane_nlines)
        gui.pane_set(1, rp_x, lp_y, rp_w, lp_h, PANE_R, pane_rplen, SPEC_R, pane_nlines)

        max_rows = left_n
        if right_n > max_rows:
            max_rows = right_n
        if max_rows < 1:
            max_rows = 1
        gui.rect(0, status_y, w, 34, 0xFFE8E8E8)
        sbuf = stack_alloc(16)
        slen = itoa(left_n, sbuf)
        _emit_text(6, 16, status_y + 8, sbuf, slen, 13, 0xFF222222)
        _emit_text(7, 48, status_y + 8, cstr(" vs "), 4, 13, 0xFF222222)
        slen = itoa(right_n, sbuf)
        _emit_text(8, 88, status_y + 8, sbuf, slen, 13, 0xFF222222)
        _emit_text(9, 128, status_y + 8, cstr(" diff ops: "), 11, 13, 0xFF222222)
        slen = itoa(n, sbuf)
        _emit_text(10, 216, status_y + 8, sbuf, slen, 13, 0xFF222222)

        gui.present()
        dirty = 0
    gui.sleep(16)
    frame = frame + 1

gui.close()
