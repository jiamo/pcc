# 第 14 章 no-libpython:把运行时收缩成内核

no-libpython 是 pcc 论题里最容易被误读的一个词。它不是"二进制里没有 C",更不是"运行时被消灭了";它的精确含义只有一句:**编译产物不依赖 CPython 运行时**。本章讲 pcc 为兑现这句话而搭建的全部机构:运行时的四层模型(C 内核保留并最小化、C 语义运行时收缩、pcc-Python 运行时增长、C-API shim 保留并规约);让 pcc-Python 能写底层代码的两扇门([pcc/extern/](../../pcc/extern) 与 [pcc/unsafe/](../../pcc/unsafe));C 与 pcc-Python 双实现的镜像纪律,以及一个多数人会答错的构建事实——默认链接的是 pcc-Python 端口,不是 C 源;最后是把"还差多少"做成单向闸门(gate)的回退棘轮([tests/fallback_baseline.json](../../tests/fallback_baseline.json))。三个战争故事都来自 2026 年 5 月的真实调查:cc 模式给出的假信心、float repr 的四条路径、以及支撑这条战线的 idiom-diff 工作法。自举不动点本身——pcc1→pcc2→pcc3——留给第 15 章;本章只回答"无 libpython 的运行时从哪里来,边界守在哪里"。

## 读者地图:no-libpython 不是"没有 C"

这一章先拆掉一个误会:no-libpython 的目标是摆脱 CPython runtime,不是把所有底层 C 内核清零。pcc 仍然需要小而稳定的 C-level kernel 来处理平台、ABI、分配、线程和 GC 原语;要收缩的是手写 C 语义层。

- C-level kernel 是机器边界,应该小而清楚。
- C semantic runtime 是要被迁移的部分,例如高层容器、dunder、异常等 Python 语义。
- pcc-Python runtime 是增长方向,它让语义可自举、可测试、可由 pcc 编译。

## 14.1 问题与设计空间:不依赖 CPython,但不是零 C

把 Python 编译成原生代码的项目都要回答同一个问题:运行时从哪里来?设计空间里有三个候选。

**备选一:链接 libpython,把 CPython 当永久运行时。** 这是最快的兼容路线:对象模型、GC、字符串、import 全部现成。代价是执行所有权仍然在 CPython 手里——你不能换 GC、不能改对象头、不能审计 import 语义,五 GC 实验室(见第 10 章)和值模型(见第 16 章)都无从谈起。pcc 不拒绝这个表面:`--python-libpython=on/auto` 模式下它仍然存在,而且是第 17 章 C 扩展兼容工作的地基。pcc 拒绝的是把它当**默认**和**终点**。

**备选二:用 C 重写一个完整的 Python 运行时。** 传统路线,得到独立产物,但制造了一个无限增长的 C 语义运行时:list、dict、str、dunder、异常、协程……每一块都是手写 C,与"用 Python 写编译器并让它编译自己"的自托管目标背道而驰。更糟的是,一旦 pcc-Python 侧也要一份(自举闭包需要),就出现两套平行的语义实现,漂移只是时间问题。

**pcc 的选择:收缩成内核。** [AGENTS.md](../../AGENTS.md) 的 Runtime layering 一节把目标写成一句话:把 C 级运行时最小化为一个 ABI 内核——分配、对象头、原子操作与引用计数屏障、平台系统调用、线程原语、动态加载、C 扩展入口、安全点(safepoint)与栈映射、GC 基元——而 Python *语义* 迁入 pcc-Python,由 pcc 自己编译。C 内核作为机器边界保留;它**不得**变成与 pcc-Python 语义运行时并行的、手工维护的第二套 C 版语义运行时。同一节明确了本章标题的语义:no-libpython 意味着不依赖 CPython 运行时,**不是**最终二进制里 C 级运行时为零。

这个选择由 CLI 上的一个三态旗标执行。`--python-libpython` 默认 `off`:任何会需要 CPython 回退(fallback)的代码生成都是硬错误;`auto` 只在确实需要时链接;`on` 始终允许。`off` 的牙齿在 [pcc/py_frontend/pipeline.py](../../pcc/py_frontend/pipeline.py) 的 `_finalize_libpython_mode()`:当模式为 `off` 且检测到回退需求时,直接抛 `PyPipelineError`,消息以 `Python pipeline requires libpython fallback for ...` 开头,并提示改用 `--python-libpython=auto/on`。失败必须响亮——这是第 5 章讲过的严格模式哲学在链接边界上的延伸。(诊断旁路 `PCC_DEBUG_LIBPYTHON_GATE_BYPASS` 存在,但它把自己打印在 stderr 上,旁路而不伪装。)

"需要回退"在 IR 层有一个可数的形态:`py_cpy_*` 调用。这族符号是 CPython 桥 ABI,实现于 [pcc/py_runtime/src/py_libpython.c](../../pcc/py_runtime/src/py_libpython.c)——`py_cpy_import` 走 `PyImport_ImportModule`,`py_cpy_getattr` 走 `PyObject_GetAttr`,以此类推。该文件的设计注释值得抄录一个决定:CPython 的 `PyObject *` 与 pcc 自己的 `PyObject *`(`py_internal.h` 定义的标记小整数 + 用户类布局)是**不同的类型**,对代码生成暴露为不透明的 `void *`,"两个指针命名空间永不混叠"。整个文件包在 `#ifdef PCC_WITH_LIBPYTHON` 里;默认构建的运行时归档根本不含真实现。于是声明卫生(claim hygiene)有了机械判据:一个声称 no-libpython 的产物,其 IR 里不允许出现 `py_cpy_*` 调用——14.5 节的棘轮就建在这条判据上。

## 14.2 四层模型:每一层一个动词

[AGENTS.md](../../AGENTS.md) 给四层各配了一个动词。这不是修辞,是任务分派:

```text
C-level kernel        KEEP(并最小化):平台/ABI、分配、原子、线程、
                      dlopen、系统调用、安全点、GC 槽位/根基元。
                      不得知道高层 Python 语义(没有 list/dict/dunder/
                      值类/import 策略;没有 if package == "numpy")。
C semantic runtime    SHRINK:手写 C 的 list/dict/str/dunder/异常语义
                      → 迁往 pcc-Python。
pcc-Python runtime    GROW:迁移目标;Python 语义用 pcc-Python 写成,
                      可自托管、可测试、由 pcc 编译。
C-API shim            KEEP(但要规约/生成):扩展看到的 ABI 表面;
                      ≠ CPython/libpython。
```

每层在仓库里有实体。**C 内核**的成员散在 [pcc/py_runtime/src/](../../pcc/py_runtime/src) 里:`pcc_threads.c`(线程原语)、`py_os_native.c`(系统调用边界)、`py_gc_index_table.c`(GC 索引基元)、`pcc_runtime_log.c`。判据不是文件大小而是知识边界:内核可以知道"指针、原子、页",不可以知道"这是一个 dict"。`if package == "numpy"` 式的特判被仓库规则点名禁止,在哪一层都一样,但在内核层尤其致命——那等于把策略焊进机器边界。

**C 语义运行时**是 `src/` 里其余的大多数:`py_list.c`、`py_dict.c`、`py_str.c`、`py_dunder.c`、`py_exc_*.c`……它们是被收缩的对象。**pcc-Python 运行时**是 [pcc/py_runtime/py/](../../pcc/py_runtime/py) 下的五十多个端口文件——`py_list.py`、`py_dict.py`、`py_obj.py`、`py_gc_backend.py`——逐文件镜像 C 实现(镜像纪律见 14.4)。**C-API shim** 是 `src/py_capi_shim.c`:不依赖 libpython、用 pcc 对象模型实现的约 286 个 `Py*` 符号,C 扩展通过它看见一个"长得像 CPython"的 ABI 表面;它的展开在第 17 章。

为什么"收缩"而不是"消灭"?因为四层中只有内核握着 pcc-Python 表达不了的东西。第 9 章见过一个具体例子:`py_obj.c` 的引用计数逻辑可以整体由端口替换,但类型专属析构器要摸柔性数组成员,留在 C。同理,`malloc` 之下没有 Python。诚实的目标是让 C 的存量单调下降、且每一块剩余的 C 都能说出自己为什么必须是 C——而不是宣称一个做不到的零。

防止"第二套规则"的不是愿望,是合同:五个 GC 后端、C 内核、pcc-Python 镜像消费**同一份**槽位级追踪/更新契约(`py_obj_visit_slots` / `py_obj_update_slot` / 根与帧注册,见第 10 章)。对象图规则只有一份;谁想私开一份,生产平等规则(Production Equality Rule)挡在前面。

## 14.3 两扇门:pcc.extern 与 pcc.unsafe

语义要迁入 pcc-Python,先得回答一个机械问题:Python 代码怎么调用 `malloc`?怎么按偏移读对象头?pcc 开了两扇门,各管一边。

**[pcc/extern/](../../pcc/extern) 管"调出去"。** `extern()` 工厂返回一个冻结的 `ExternFn` dataclass:符号名、参数 C 类型元组、返回类型、是否变参。类型标记(`c_int64`、`c_ptr`、`c_str`……)是 `_CType` 实例,一一对应 LLVM IR 类型。模块 docstring 给的例子:

```python
from pcc.extern import extern, c_int, c_str

printf: extern = extern(
    "printf", argtypes=(c_str,), restype=c_int, variadic=True,
)
```

关键在低层化(lowering)语义:前端([pcc/py_frontend/codegen/extern_lowering.py](../../pcc/py_frontend/codegen/extern_lowering.py) 的 `ExternScaffoldMixin`,配合 layer1 的 extern 调用发射)把每个经由 `ExternFn` 的调用点改写为对命名外部符号的**直接 LLVM `call`**——没有 Python 蹦床、没有 `py_obj_*` 中转,发射出的机器码就是一条 `bl <symbol>` 加必要的整型截断/扩展。`ExternFn.__call__` 本体是一个运行时陷阱:它只在 CPython 解释执行时可达(即开发 pcc 自身时),一到就响亮地 `raise NotImplementedError`。这个方法被刻意写成无参数——`*args` 签名会触发自托管审计(pcc 前端不低层化变参 def),而既然前端在所有调用点完成改写,运行时签名本就无关紧要。一个为了"在两个世界里都合法"而做的小设计,典型的自托管税。

`extern` 还有一个反向装饰器 `c_abi_export(symbol)`:强制 pcc 用给定的、未混淆的 C ABI 符号名发射被装饰函数,而不是通常的 `user_<module>_<name>` 混淆名。这是端口替换 C 的钩子——`py_str_accessors.py` 里 `@c_abi_export("py_str_len")` 修饰的函数,编译后在链接期顶替 `py_str_accessors.c` 导出的同名符号。装饰器在 Python 层是空操作,只为让端口文件在 CPython 下仍可被导入。

**[pcc/unsafe/](../../pcc/unsafe) 管"摸内存"。** 它"刻意不是一个普通的运行时库"(模块 docstring 原话):前端在编译期识别来自 `pcc.unsafe` 的导入,把每个调用低层化为裸 LLVM/平台操作——`malloc`/`free`、`load_i8` 到 `store_f64` 的全谱定宽读写、`ptr_add`/`ptr_diff`、标记整数操作 `is_tagged_int`/`tag_int`/`untag_int`、`memcpy`、甚至 `call_ptr1` 这样的间接调用基元。识别机制在 [pcc/py_frontend/codegen/unsafe_lowering.py](../../pcc/py_frontend/codegen/unsafe_lowering.py):一个 `UNSAFE_INTRINSICS` 冻结集合列出全部内在函数(intrinsics)名。CPython 下每个函数都落进 `_trap()`,同样响亮地拒绝执行。

两扇门合起来,端口文件就是"用 Python 的语法写 C 的语义"。看 [pcc/py_runtime/py/py_str_accessors.py](../../pcc/py_runtime/py/py_str_accessors.py) 的实战形态:文件头先把 `PyStrObject` 布局抄成注释合同(`byte_len` 在偏移 16,`cp_len` 在 24,`hash` 在 32,UTF-8 数据从 40 开始),然后:

```python
@c_abi_export("py_str_len")
def py_str_len(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    cp: int = load_i64(s, 24)
    if cp < 0:
        cp = _utf8_codepoint_count(ptr_add(s, 40), load_i64(s, 16))
        store_i64(s, 24, cp)
    return cp
```

读偏移 24 的码点数缓存,未计算(-1)则数一遍 UTF-8 并写回——与 C 版逐步对应,包括惰性缓存这个副作用。布局注释不是文档礼貌,是镜像纪律的载体:C 结构体一旦变,端口的魔法偏移就静默读错,而第 7 章讲过的布局漂移类 bug 正是这么来的。

诚实部分:这扇门还有已知的锋利边缘,[pcc/extern/__init__.py](../../pcc/extern/__init__.py) 的 docstring 自己列了清单。`c_str` **参数**位上,pcc 的 `str` 值是 `PyStrObject*`,而 extern 声明期待裸 `char*`,当前代码生成原样传过——对直接读字节的 libc 符号是错的,多数调用方靠先物化 NUL 结尾缓冲的辅助函数绕开;`c_str` **返回**位上(如 `getenv`)拿回的是裸 `i8*`,尚无运行时辅助把它包成 `PyStrObject`;errno 不映射为 Python 异常,想要 `OSError` 语义的包装层必须自己查 errno。这些以开放问题的身份写在源码里,而不是被假装不存在。

## 14.4 构建事实:默认链接的是端口,不是 C 源

本节是全章杠杆最高的一节,因为它纠正一个几乎人人都会先入为主的假设:既然 `src/*.c` 里有一份"真"运行时,默认构建链接的当然是它?**不是。**

[pcc/py_runtime/Makefile](../../pcc/py_runtime/Makefile) 维护四个归档:

```text
libpy_runtime.a                cc 基线:host C 编译器编 src/*.c
libpy_runtime_pcc.a            同一批 C 源,由 pcc 的 C 前端编译(oracle 之二)
libpy_runtime_pcc_py.a         pcc-Python 端口 + C-only 辅助对象(默认)
libpy_runtime_pcc_py_libpython.a   上者加 CPython 桥(兼容回退用)
```

第三个归档的配方是本节核心。Makefile 的 `PY_MODULES` 变量列出 55 个模块名——`py_obj`、`py_dict`、`py_list`、`py_str_accessors`、`py_gc_backend`、`py_int_*` 全家……每个名字对应 `py/` 下一个端口文件。构建规则对每个端口执行两步:`pcc --python-library --emit-llvm` 把 `.py` 编成库形态的 LLVM IR(不合成程序 `@main`),再由 `pcc.tools.ir_to_obj` 降为可重定位对象。归档由这些 Python 出身的 `.o` 直接构成;注释写明理由:no-C 闭包不得依赖"在每个目标上成功编译被替换的 C 运行时源文件"。配套的 `PY_REPLACED_C_MODULES` 还多列一个 `py_bytes`——`py_obj_stubs.py` 拥有导出的 bytes 辅助函数,所以 C 的 `py_bytes.o` 必须从该归档剔除,免得符号撞车。

选择哪个归档,由 [pcc/py_frontend/pipeline.py](../../pcc/py_frontend/pipeline.py) 的两个函数决定,而它们的默认值就是本节标题:`_runtime_cc_mode()` 读 `PCC_RUNTIME_CC`,默认返回 `"pcc"`;`_runtime_high_mode()` 读 `PCC_RUNTIME_HIGH`,默认返回 `"py"`。两个默认叠加,`_ensure_runtime()` 选中 `libpy_runtime_pcc_py.a`。docstring 直说:默认就是自举安全路径,`PCC_RUNTIME_CC=cc` 是**显式要求 host-cc oracle 归档**时才设的。

这个事实给镜像纪律装上了牙齿。[AGENTS.md](../../AGENTS.md) 的规则是"Mirror C and pcc-Python runtimes"——多数 GC/运行时代码在 `src/` 有 C 文件、在 `py/` 有端口,两者必须同步。现在可以把它说成构建语言:**对 PY_MODULES 名单内的文件,改 C 源对默认模式是无效操作**。C 版只活在 cc/oracle 归档里;默认模式的二进制链接的是端口的代码。反方向同理,只改端口则 oracle 归档失真。14.6 的第一个战争故事就是这条纪律被违反的完整记录。

那哪些 C 可以**不**镜像?Makefile 的 `OBJ_PY_CC_HELPERS` 列表给出答案:`py_format.o`、`py_capi_shim.o`、`py_extension_loader.o`、`pcc_threads.o`、`py_gc_index_table.o`、`py_os_native.o`、`py_re_engine.o`、`py_cpy_handle.o` 等二十来个对象,以 host cc 编译后**直接进入端口归档**。它们是 C-only 辅助:单一 C 实现、没有端口镜像、两种归档共享同一份语义。仓库的取舍规则:当一个新的 no-libpython 运行时辅助函数让端口去重写会很笨拙(需要 C 库函数循环、平台边界、或与 shim 共生)时,加一个 C-only 文件,而不是 C+端口双写。这不是镜像纪律的漏洞,是它的补集——镜像的成本只花在"语义真的要迁移"的文件上;14.6 的 float repr 故事会展示一次教科书式的运用。

最后一个归档讲完边界才完整。`libpy_runtime_pcc_py_libpython.a` 从端口归档复制而来,加入 `py_libpython.o`,但同时**删除** `py_capi_shim.o`。Makefile 注释解释了为什么:libpython 模式下真正的 libpython 提供 CPython C-API;而 dlopen 的扩展以 `-undefined dynamic_lookup` 构建,其 `Py*` 调用从可执行文件的全局符号解析——如果 shim 还在,它会**遮蔽**真 C-API,用 pcc 的对象模型错误处置真 CPython 对象,直接打碎 `import numpy` 这类扩展初始化。一个归档里只能有一个 C-API 提供者:这是 mode 边界(libpython vs no-libpython)在链接器层面的物化。

## 14.5 回退棘轮:把"还差多少"做成单向闸门

收缩是一条长战线,需要一个不会说谎的进度计。pcc 的做法:把回退做成**可数、可解释、可锁定**的三件套。

**可数。** [scripts/probe_stage1_closure.py](../../scripts/probe_stage1_closure.py) 以 [pcc/__main__.py](../../pcc/__main__.py) 为入口算出 stage1 紧闭包(基线记录 27 个文件),对闭包做两种代码生成:逐模块独立编译,和多文件合并编译;然后用一个正则数 IR 文本里的 `call ... @py_cpy_` 出现次数。计数器对每个 `py_cpy_*` 调用点一视同仁——14.1 节说过,这族符号存在即意味着需要 CPython 桥。

**可锁定。** [tests/python/test_fallback_baseline.py](../../tests/python/test_fallback_baseline.py) 把计数变成棘轮。规则全部在源码里:`_RATCHET_PERCENT = 5.0`,`_within_ratchet()` 允许不超过基线 5%(至少 +1)的噪声增长,缩小永远通过;迁移成功后,基线 JSON 以更低数字重捕获——棘轮单向,"allow shrink, forbid growth"。逐模块也有棘轮,且带一条隐式零规则:不在基线里的模块视为 0,必须保持 0,"a previously-clean module regressed" 是显式失败消息——已经赢下的地盘不许悄悄丢回去。

最精细的一笔是**分账**。桥接符号 `py_cpy_to_pcc_obj` / `py_cpy_to_pcc_str`(CPython 值摆渡回 pcc 对象的临时桥)与其余 `py_cpy_*` 分开计数,且 ON 模式下两个账户用严格 `<=` 断言、不享受 5% 棘轮。`test_on_mode_bridge_calls_do_not_regress` 的失败消息把动机写成了一句格言:桥调用仍然需要 libpython,所以"靠加桥调用来减少非桥 `py_cpy_*` 不算 Issue 1 的进步"。没有这笔分账,指标可以被"把回退改名成桥"刷绿。

[tests/fallback_baseline.json](../../tests/fallback_baseline.json) 本身值得当史料读。它同时锁多文件总量、逐模块独立计数、按动作(`call`/`getattr`/`setattr`/…)与按符号的诊断聚合;而 `_recapture_log` 数组是一部带日期的削减编年史:OFF 模式多文件总数从 2026-04-28 的 27853,经原生 os.path 分派、cpy→pcc 字面量桥、跨模块类分派、原生文件对象等一波波切片,到 2026-04-29 ON 模式归零、2026-05-01 多文件全零——那一天的条目写着:"Issue 1 strict bootstrap closure: pcc1/pcc2/pcc3 no longer link libpython"。每条记录都有 delta 和 reason;读它比读任何叙事文档更接近这条战线的真实节奏。今天的总量字段是 `fallbacks_total: 0`,JSON 头部注释直接写明:现在必须保持为零。

**可解释。** 计数告诉你多少,不告诉你为什么。[pcc/fallback_routes.py](../../pcc/fallback_routes.py) 把流水线的 import 分类决定转成带稳定理由的事件:`compile_time_only`(编译期擦除)、`native_user_module`(编入源闭包)、`builtin_native_dispatch`(内建原生分派)、`native_stdlib`(解析到 [pcc/py_stdlib](../../pcc/py_stdlib) 提供者)、`cpython_fallback`("no native provider found; libpython required unless disabled")。[pcc/fallback_explainer.py](../../pcc/fallback_explainer.py) 的 `FallbackReason` 再带上 phase、suggestion(例如"add pcc/py_stdlib port or enable --python-libpython=auto")与 source,可序列化为 `pcc.fallback.v1` JSON。CLI 旗标 `--explain-fallback` 在 [pcc/cli_core.py](../../pcc/cli_core.py) 与 [pcc/cli_bootstrap.py](../../pcc/cli_bootstrap.py) 都接了线。设计立场与第 1 章的声明卫生同源:回退不是耻辱,**不可见的回退**才是;每一次回退要么被解释,要么被禁止。

## 14.6 历史与教训

三个故事按依赖顺序讲:先是产出切片的工作法,然后是这套工作法两次撞上本章机构时留下的教训。素材来自 [docs/current-goal-state.md](../../docs/current-goal-state.md) 2026-05-29/30 的证据记录与相关调查。

### 14.6.1 idiom-diff 工作法:有界切片的来源

no-libpython 战线上最危险的工作方式是"凭感觉找下一个洞"。2026-05-29 起固化下来的替代品叫 **CPython idiom-diff**:写一批常见习语(浮点格式规格、`%` 格式化、`bin()/hex()/oct()`、`round()`、str 方法、`set ^`、`divmod`、元组解包、`sorted` 各形态……),在 `--backend self --python-libpython=off` 下编译运行,与 `python3` 的输出逐条 diff。每一处不一致就是一个**有界切片**:症状明确、修复面小、可独立验证。纪律是一次一个切片,每个切片配最小回归测试,且——因为这些路径全在自举闭包上——必须跑完整三阶段自举闸门才算完(自举闸门的内涵见第 15 章)。

这套方法一个会话交付了 9 个自举验证的切片(浮点宽度/精度、`%` 规格、bin/hex/oct、round 的银行家舍入、str 大小写方法、set 对称差、divmod 浮点、浮点元组解包类型、sorted 处理 dict/可迭代)。同样有价值的是两次**失败**:为 `list(generator)` 和列表越界 IndexError 各加一个新运行时符号(`py_obj_to_list`、`py_list_get_checked`)并让代码生成调用,两次都在 stage2 以相同方式失败——默认端口归档没有可靠拾取新符号。对照同会话全部成功的"修改既有运行时函数"型切片,留下一条实践规则:**改既有函数自举安全;加新符号给 codegen 调用是自举风险**,需要构建系统层面的处理。还有一条测量纪律的教训:OOB 尝试最初被误诊为"stale runtime link",真正原因是改错了前端路径——типед 列表的下标读取走 `exact_int_lowering.py` 而非 `subscript_lowering.py`(双路径问题见第 6 章);定位手段不是猜缓存,而是 dump IR 看它到底调了哪个符号。

### 14.6.2 PCC_RUNTIME_CC=cc 的假信心(2026-05-30)

**症状。** 上述 9 个切片全部回归测试绿、自举闸门绿。随后一个默认模式探针调用 `bin(5)`,链接失败:`undefined _py_builtin_bin`。

**错误假设。** "回归测试 + 自举全绿 = 切片在目标模式下成立。"两个闸门各有一个此前没人说破的盲区。

**证据链。** 逐项核对发现,9 个切片里 4 个只在 cc 模式生效:bin/hex/oct(新符号加在 `py_dunder.c`,端口 `py_dunder.py` 没有)、str 的 title/swapcase/casefold(`py_str_accessors.c` 新符号,端口缺失)、`set ^`(`py_set.c` 的 `py_set_symmetric_difference`,端口缺失)、sorted 处理 dict/可迭代(`py_obj_ops_compare.c` 的 `py_obj_sorted` 改了,端口还是旧的整数下标版本——这个最阴险:默认模式不是链接失败,而是**静默保留旧的错误行为**)。这四个文件全在 `PY_MODULES` 名单里;而所有回归测试都钉了 `PCC_RUNTIME_CC=cc`,只验证了 oracle 归档。自举为什么也绿?因为 pcc 自己的代码不调用 bin、不用 set 对称差——**闸门通过只证明闸门覆盖的路径,不证明特性存在**。

**真正根因。** 14.4 的构建事实:默认模式链接端口归档,`PY_MODULES` 文件的 C 修改对默认模式不可见。[AGENTS.md](../../AGENTS.md) 的镜像规则一直在,但在"测试钉死 cc 模式"的工作流里没有牙齿。

**修复与不变式。** 按 port-mirror 模式逐个补镜像:先 str-case(把 swapcase/title/casefold 按 `py_str_upper` 的风格用 `load_i8`/`store_i8` 写进端口),默认模式探针对齐 CPython,回归测试**改为默认模式**(删掉 `PCC_RUNTIME_CC=cc`),自举 48.47 秒通过——第一个 cc-only 切片被"做成真的"。留下的不变式:(1) no-libpython 的测试与探针必须跑默认模式,cc 是 oracle 不是目标;(2) `PY_MODULES` 文件的运行时修复,C 与端口必须同一变更内镜像;(3) 评估一个"绿"之前,先问它跑在哪个模式上——这正是第 1 章 mode-labeled claims 在测试工程里的版本。

### 14.6.3 float repr 的四条路径与 C-only 收敛(2026-05-29)

**症状。** idiom-diff 报 `print(10/3)`:CPython 输出 `3.3333333333333335`,pcc 输出 `3.333333`。看似一个格式化精度小修。

**错误假设。** "改 C 的打印格式化就行。"第一次尝试在 `py_print_fmt.c` 与 `py_dunder.c` 做最短往返修复,随即被回退(revert)。

**证据链。** 调查确认浮点→字符串在运行时里**至少四个站点**,且按运行时模式分裂:默认模式(`PCC_RUNTIME_HIGH=py`)的 print 走端口 `py_print_fmt.py` 的 `_format_float`——固定 6 位小数,对非有限值输出垃圾(inf 打成 `9223372036854.775807`);cc 模式走 C `py_print_fmt.c` 的 `%g`(6 位有效数字)——两个模式连错法都不一样。`str()`/`repr()` 另有端口站点 `py_obj_stubs.py` 的 `_float_str`(同样固定 6 位),C 侧还有 `py_dunder.c`。只修 C 等于只修 oracle 模式,优先级最高的默认模式纹丝不动——这是 14.6.2 同一构建事实的另一张面孔,而且雪上加霜:正确算法(递增精度试探 + `strtod` 回检)在每个端口里用 `pcc.unsafe` 重写一遍,既笨拙又必然漂移。

**真正根因与修复。** 问题不是某一站点的格式串,而是**同一语义散布在四个实现点**。修复用的正是 14.4 的 C-only 辅助规则:在 [pcc/py_runtime/src/py_format.c](../../pcc/py_runtime/src/py_format.c)(`OBJ_PY_CC_HELPERS` 成员,两种归档共享)加单一实现 `py_float_repr_shortest()`——nan/±inf/带符号零特判;从 1 到 17 位有效数字逐次 `%.*e` 格式化并用 `strtod` 回检,取第一个能精确往返的位数(17 位必定往返 IEEE-754 double);按 CPython 的阈值(指数在 [-4, 16) 用定点记法)选记法;整数值浮点补 `.0`。函数注释言明合同:**两个运行时层级**的 str/repr/print 都走它;端口 `py_print_fmt.py` 与 `py_obj_stubs.py` 各自用一行 `extern("py_float_repr_shortest", (c_ptr,), c_ptr)` 声明并调用,旧的固定 6 位路径退役。

**不变式。** 修语义先数路径:同一语义有 N 个实现点时,修 1 个不是修复的 1/N,是引入第 N+1 种行为。收敛手段按层级选:语义该迁 Python 的,镜像;算法笨重且层级无关的,C-only 单点 + extern 声明。浮点 repr 属于后者。

## 14.7 小结

no-libpython 是一个边界声明,不是一个清零声明:产物不依赖 CPython 运行时,而 C 以"内核"的身份合法存续。本章的机构围绕三个守边动作展开。**分层**:四层模型给每层一个动词(KEEP-minimize / SHRINK / GROW / KEEP-spec),C 内核以知识边界而非代码量定义,`py_cpy_*` 桥被隔离在 `py_libpython.c` 的 `#ifdef` 后面,两个指针命名空间永不混叠。**迁移**:`pcc.extern` 与 `pcc.unsafe` 让 pcc-Python 写底层,`c_abi_export` 让端口在链接期顶替 C 符号;Makefile 的 `PY_MODULES` 把 55 个端口编进默认归档——默认链接端口而非 C 源,这个构建事实是镜像纪律的牙齿,也是两个战争故事共同的根。**锁定**:回退棘轮把"还差多少"做成 5% 噪声上限、单向、桥/非桥分账的闸门,`_recapture_log` 留下从 27853 到 0 的编年史;`--explain-fallback` 保证每次回退要么被解释、要么被禁止。三个故事的公约数是测量纪律:闸门只证明它覆盖的模式,绿不等于真——先问"跑在哪个模式上",再决定相信什么。pcc1→pcc2→pcc3 如何用这套运行时闭合不动点,见第 15 章。

## 练习

1. **读源码验证。** 读 [pcc/py_runtime/Makefile](../../pcc/py_runtime/Makefile):数一数 `PY_MODULES` 列表的模块数;解释 `PY_REPLACED_C_MODULES` 为什么比它多一个 `py_bytes`,以及对应的 bytes 辅助函数住在哪个端口文件里。再读 [pcc/py_frontend/pipeline.py](../../pcc/py_frontend/pipeline.py) 的 `_runtime_cc_mode()` 与 `_runtime_high_mode()`,写出环境变量未设置、设为非法值、设为 `"host"` 三种情况下各返回什么。

2. **读源码验证。** 对照 [pcc/py_runtime/py/py_str_accessors.py](../../pcc/py_runtime/py/py_str_accessors.py) 的 `py_str_len` 与 `py_internal.h` 中 `PyStrObject` 的布局:`load_i64(s, 24)` 读的是哪个字段?为什么这个函数有写副作用(`store_i64`)?如果有人在 C 结构体 `byte_len` 之前插入一个新的 8 字节字段而不改端口,会发生什么,哪个章节的哪类 bug 会先暴露?

3. **棘轮设计论证。** [tests/python/test_fallback_baseline.py](../../tests/python/test_fallback_baseline.py) 对 ON 模式总量用 `_within_ratchet`(基线 0 时数学上容忍 actual=1),对 bridge 与 non-bridge 两个分账却用严格 `<=`。论证这个组合为什么仍然守得住"多文件零回退"——并指出如果**只有**带百分比棘轮的总量、没有分账,哪两类回归会被放进来。

4. **设计权衡。** [pcc/extern/__init__.py](../../pcc/extern/__init__.py) 自述了 `c_str` 返回位的缺口:`getenv` 拿回裸 `i8*`,无人包装成 `PyStrObject`。给出两个修复方向——(a) 加一个运行时辅助函数(C-only?端口镜像?)在调用后包装;(b) 让代码生成在 `c_str` 返回位自动插入包装调用——并从 14.4 的归档结构与 14.2 的层边界论证各自的归属与风险:新符号会进哪些归档?哪个方向更接近 14.6.1 提到的"新符号自举风险"?

5. **战争故事推演。** 假设你要给 `py_set.c` 新增 `py_set_symmetric_difference` 并让前端低层化 `a ^ b` 调用它。按本章与 14.6 的教训,列出让它在**默认模式**下真实生效的完整步骤清单(运行时 C、端口、`runtime_abi` 声明、回归测试的模式选择、自举闸门),并标出哪一步被省略时,会精确复现 14.6.2 的四种假信心形态中的哪一种(链接失败 vs 静默旧行为)。
