"""Composition-tree window: the kernel drives a real window.

Builds a tree (header + two panes + diff rows as nodes), updates node
geometry/colour/text per frame, renders via the kernel's tree walk, and
hands the rect/text commands to the bridge via gui.render_scene.  This
proves the composition-tree kernel is the actual rendering driver.

Run: ./kit_window  (window with dual-pane diff from samples)
"""

from pcc.extern import c_ptr, c_int64, c_int32, extern
from pcc.unsafe import (
    calloc, cstr, define_global_i64, global_addr, int_to_ptr, load_i32,
    load_i64, load_i8, ptr_add, ptr_to_int, stack_alloc, store_i32,
    store_i64, store_i8, wrapping_mul_i64,
)
import pcc_gui_high as gui
from diff_core import pcc_gui_diff_init, pcc_gui_diff_compute

# ---------- kernel (inlined, module-level functions) ----------
define_global_i64("kit_pool", 0)
define_global_i64("kit_count", 0)
define_global_i64("kit_cap", 0)


def _na(idx: int) -> int:
    return load_i64(global_addr("kit_pool"), 0) + idx * 128


def kit_init(cap: int) -> int:
    store_i64(global_addr("kit_pool"), 0, ptr_to_int(calloc(cap * 128, 1)))
    store_i64(global_addr("kit_cap"), 0, cap)
    store_i64(global_addr("kit_count"), 0, 0)
    return 0


def kit_create(parent: int) -> int:
    count = load_i64(global_addr("kit_count"), 0)
    if count >= load_i64(global_addr("kit_cap"), 0):
        return -1
    idx = count
    store_i64(global_addr("kit_count"), 0, count + 1)
    n = int_to_ptr(_na(idx))
    store_i64(n, 0, parent)
    store_i64(n, 8, -1)
    store_i64(n, 16, -1)
    store_i64(n, 24, -1)
    store_i32(n, 80, 1)
    store_i32(n, 84, 0)
    store_i32(n, 88, 0)
    store_i32(n, 92, 0xFFFFFFFF)
    store_i64(n, 96, 0)
    store_i64(n, 104, 0)
    store_i64(n, 112, 0)
    store_i64(n, 120, 0)
    if parent >= 0:
        first = load_i64(int_to_ptr(_na(parent)), 8)
        if first < 0:
            store_i64(int_to_ptr(_na(parent)), 8, idx)
        else:
            sib = first
            while load_i64(int_to_ptr(_na(sib)), 16) >= 0:
                sib = load_i64(int_to_ptr(_na(sib)), 16)
            store_i64(int_to_ptr(_na(sib)), 16, idx)
            store_i64(int_to_ptr(_na(idx)), 24, sib)
    return idx


def kit_rect(idx: int, x: int, y: int, w: int, h: int, color: int) -> None:
    n = int_to_ptr(_na(idx))
    store_i64(n, 32, x)
    store_i64(n, 40, y)
    store_i64(n, 48, w)
    store_i64(n, 56, h)
    store_i32(n, 92, color)


def kit_text(idx: int, x: int, y: int, tp, tlen: int, font: int, color: int) -> None:
    n = int_to_ptr(_na(idx))
    store_i64(n, 32, x)
    store_i64(n, 40, y)
    store_i64(n, 48, tlen * 7)
    store_i64(n, 56, font)
    store_i32(n, 88, 2)
    store_i32(n, 92, color)
    store_i64(n, 104, tlen)
    store_i64(n, 112, font)
    store_i64(n, 120, ptr_to_int(tp))


def kit_render(idx: int, rects, colors, rn_out, texts, tn_out) -> None:
    if load_i32(int_to_ptr(_na(idx)), 80) == 0:
        return
    t = load_i32(int_to_ptr(_na(idx)), 88)
    bg = load_i32(int_to_ptr(_na(idx)), 92)
    if bg == -1:
        parent = load_i64(int_to_ptr(_na(idx)), 0)
        if parent >= 0:
            bg = load_i32(int_to_ptr(_na(parent)), 92)
    if t == 0 and bg != -1:
        _rect_cmd(idx, rects, colors, rn_out, bg)
    if t == 1:
        _rect_cmd(idx, rects, colors, rn_out, bg)
    elif t == 2:
        tn = load_i64(tn_out, 0)
        fl = load_i64(int_to_ptr(_na(idx)), 112)
        store_i64(texts, tn * 48 + 0, load_i64(int_to_ptr(_na(idx)), 32))
        store_i64(texts, tn * 48 + 8, load_i64(int_to_ptr(_na(idx)), 40))
        store_i64(texts, tn * 48 + 16, load_i64(int_to_ptr(_na(idx)), 104))
        store_i64(texts, tn * 48 + 24, fl & 0xFFFF)
        store_i64(texts, tn * 48 + 32, load_i32(int_to_ptr(_na(idx)), 92))
        store_i64(texts, tn * 48 + 40, load_i64(int_to_ptr(_na(idx)), 120))
        store_i64(tn_out, 0, tn + 1)
    child = load_i64(int_to_ptr(_na(idx)), 8)
    while child >= 0:
        kit_render(child, rects, colors, rn_out, texts, tn_out)
        child = load_i64(int_to_ptr(_na(child)), 16)


def _rect_cmd(idx: int, rects, colors, rn_out, bg: int) -> None:
    rn = load_i64(rn_out, 0)
    store_i64(rects, rn * 32 + 0, load_i64(int_to_ptr(_na(idx)), 32))
    store_i64(rects, rn * 32 + 8, load_i64(int_to_ptr(_na(idx)), 40))
    store_i64(rects, rn * 32 + 16, load_i64(int_to_ptr(_na(idx)), 48))
    store_i64(rects, rn * 32 + 24, load_i64(int_to_ptr(_na(idx)), 56))
    store_i32(colors, rn * 4 + 0, (bg >> 16) & 255)
    store_i32(colors, rn * 4 + 1, (bg >> 8) & 255)
    store_i32(colors, rn * 4 + 2, bg & 255)
    store_i32(colors, rn * 4 + 3, 255)
    store_i64(rn_out, 0, rn + 1)


# ---------- file reading + diff ----------
py_program_argc_fn = extern("py_program_argc", (), c_int32)
py_program_argv_fn = extern("py_program_argv", (c_int64,), c_ptr)
open_fn = extern("open", (c_ptr, c_int32), c_int64)
read_fn = extern("read", (c_int64, c_ptr, c_int64), c_int64)
close_fn = extern("close", (c_int64,), c_int64)

IDS_L = calloc(16384, 1)
IDS_R = calloc(16384, 1)
BUF_L = calloc(65536, 1)
BUF_R = calloc(65536, 1)
LINES = calloc(32768, 1)


def read_lines(path, buf, ids, lines) -> int:
    fd = open_fn(path, 0)
    total = read_fn(fd, buf, 65536)
    close_fn(fd)
    count = 0
    start = 0
    h = -7046029254386353131
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


lp = cstr("/tmp/a.txt")
rp = cstr("/tmp/b.txt")
if py_program_argc_fn() >= 3:
    lp = py_program_argv_fn(1)
    rp = py_program_argv_fn(2)
left_n = read_lines(lp, BUF_L, IDS_L, LINES)
right_n = read_lines(rp, BUF_R, IDS_R, LINES)
pcc_gui_diff_init(256)
ops = stack_alloc(6144)
n = pcc_gui_diff_compute(IDS_L, left_n, IDS_R, right_n, ops, 256)

# ---------- window + tree ----------
gui.init(cstr("kit tree"), 900, 600,
         cstr("/Users/jiamo/my/pcc/projects/mac_diff_app/libpcc_gui_metal.dylib"))
W = 900
H = 600

kit_init(256)
root = kit_create(-1)
kit_rect(root, 0, 0, W, H, 0xFFFFFFFF)
header = kit_create(root)
kit_rect(header, 0, 0, W, 48, 0xFFE8E8E8)
lc = kit_create(root)
kit_rect(lc, 8, 92, 420, H - 130, 0xFFFFFFFF)
rc = kit_create(root)
kit_rect(rc, 436, 92, 420, H - 130, 0xFFFFFFFF)

MAXR = 24
row_l = calloc(MAXR * 8, 1)
row_r = calloc(MAXR * 8, 1)
tn_l = calloc(MAXR * 8, 1)
tn_r = calloc(MAXR * 8, 1)
ln_l = calloc(MAXR * 8, 1)
ln_r = calloc(MAXR * 8, 1)
i = 0
while i < MAXR:
    rl = kit_create(lc)
    rr = kit_create(rc)
    store_i64(row_l, i * 8, rl)
    store_i64(row_r, i * 8, rr)
    kit_rect(rl, 0, i * 20, 420, 18, 0xFFF8F8F8)
    kit_rect(rr, 0, i * 20, 420, 18, 0xFFF8F8F8)
    l1 = kit_create(rl)
    t1 = kit_create(rl)
    l2 = kit_create(rr)
    t2 = kit_create(rr)
    store_i64(ln_l, i * 8, l1)
    store_i64(tn_l, i * 8, t1)
    store_i64(ln_r, i * 8, l2)
    store_i64(tn_r, i * 8, t2)
    i = i + 1

rects = calloc(256 * 32, 1)
colors = calloc(256 * 4, 1)
texts = calloc(256 * 48, 1)
rn = stack_alloc(8)
tn = stack_alloc(8)


def itoa(v: int, buf) -> int:
    tmp = stack_alloc(16)
    k = 0
    if v == 0:
        store_i8(buf, 0, 48)
        return 1
    while v > 0:
        store_i8(tmp, k, 48 + (v % 10))
        v = v // 10
        k = k + 1
    j = 0
    while j < k:
        store_i8(buf, j, load_i8(tmp, k - 1 - j))
        j = j + 1
    return k


frame = 0
while gui.running():
    li = 0
    ri = 0
    oi = 0
    vis = 0
    while oi < n and vis < MAXR:
        t = load_i64(ops, oi * 24 + 0)
        if t == 0:
            # equal: both panes
            lno = load_i64(ops, oi * 24 + 8)
            rno = load_i64(ops, oi * 24 + 16)
            kit_rect(load_i64(row_l, vis * 8), 0, 0, 420, 18, 0xFFF8F8F8)
            kit_rect(load_i64(row_r, vis * 8), 0, 0, 420, 18, 0xFFF8F8F8)
            nb = stack_alloc(8)
            nl = itoa(lno + 1, nb)
            kit_text(load_i64(ln_l, vis * 8), 0, 0, nb, nl, 11, 0xFF999999)
            off = load_i64(LINES, lno * 16)
            ln = load_i64(LINES, lno * 16 + 8)
            kit_text(load_i64(tn_l, vis * 8), 34, 0, ptr_add(BUF_L, off), ln, 13, 0xFF333333)
            nb2 = stack_alloc(8)
            nl2 = itoa(rno + 1, nb2)
            kit_text(load_i64(ln_r, vis * 8), 0, 0, nb2, nl2, 11, 0xFF999999)
            off2 = load_i64(LINES, rno * 16)
            ln2 = load_i64(LINES, rno * 16 + 8)
            kit_text(load_i64(tn_r, vis * 8), 34, 0, ptr_add(BUF_R, off2), ln2, 13, 0xFF333333)
            li = li + 1
            ri = ri + 1
            vis = vis + 1
        elif t == 1:
            lno = load_i64(ops, oi * 24 + 8)
            kit_rect(load_i64(row_l, vis * 8), 0, 0, 420, 18, 0xFFF0C8C8)
            nb = stack_alloc(8)
            nl = itoa(lno + 1, nb)
            kit_text(load_i64(ln_l, vis * 8), 0, 0, nb, nl, 11, 0xFF999999)
            off = load_i64(LINES, lno * 16)
            ln = load_i64(LINES, lno * 16 + 8)
            kit_text(load_i64(tn_l, vis * 8), 34, 0, ptr_add(BUF_L, off), ln, 13, 0xFF8A2A2A)
            li = li + 1
            vis = vis + 1
        else:
            rno = load_i64(ops, oi * 24 + 16)
            kit_rect(load_i64(row_r, vis * 8), 0, 0, 420, 18, 0xFFC8F0C8)
            nb2 = stack_alloc(8)
            nl2 = itoa(rno + 1, nb2)
            kit_text(load_i64(ln_r, vis * 8), 0, 0, nb2, nl2, 11, 0xFF999999)
            off2 = load_i64(LINES, rno * 16)
            ln2 = load_i64(LINES, rno * 16 + 8)
            kit_text(load_i64(tn_r, vis * 8), 34, 0, ptr_add(BUF_R, off2), ln2, 13, 0xFF2A6A2A)
            ri = ri + 1
            vis = vis + 1
        oi = oi + 1
    while vis < MAXR:
        kit_rect(load_i64(row_l, vis * 8), 0, 0, 420, 18, 0xFFFFFFFF)
        kit_rect(load_i64(row_r, vis * 8), 0, 0, 420, 18, 0xFFFFFFFF)
        vis = vis + 1

    store_i64(rn, 0, 0)
    store_i64(tn, 0, 0)
    kit_render(root, rects, colors, rn, texts, tn)
    gui.render_scene(rects, colors, load_i64(rn, 0), texts, load_i64(tn, 0), W, H)
    gui.sleep(16)
    frame = frame + 1

gui.close()
