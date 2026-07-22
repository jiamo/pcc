# 附录 B 术语对照表

本书固定译法。代码标识符、CLI 旗标、环境变量一律保留英文原样。

## 编译器

| English | 中文 | 注 |
|---|---|---|
| lowering | 低层化 | 把高层构造翻译为低层 IR 的过程 |
| codegen | 代码生成 | |
| frontend / backend | 前端 / 后端 | |
| translation unit (TU) | 翻译单元(TU) | |
| merged directory mode | 合并目录模式 | 目录输入的默认编译模式 |
| constant folding | 常量折叠 | 在 pcc 中是语义子系统,不是单纯优化 |
| usual arithmetic conversions | 一般算术转换 | C 标准术语 |
| signedness | 符号性 | i32 位形之外单独跟踪的属性 |
| oracle | 基准参照(oracle) | 已知正确的对照实现 |
| parity | 对齐/一致性(parity) | 两实现产出一致 |
| fallback | 回退 | 进入 CPython 桥的边界 |
| scaffold(`--ir-scaffold`) | 脚手架 | 闭世界低层化模式 |
| fail loudly | 响亮失败 | 与静默错误相对 |

## 运行时与 GC

| English | 中文 | 注 |
|---|---|---|
| object header | 对象头 | `PyObjectHeader` |
| type tag | 类型标签 | `enum PyTypeTag` |
| refcount | 引用计数 | |
| owned / borrowed reference | 拥有引用 / 借用引用 | 所有权契约的两极 |
| retain / release | 持有 / 释放 | |
| write / read barrier | 写屏障 / 读屏障 | `pcc_gc_store_ptr` / `pcc_gc_load_ptr` |
| remembered set | 记忆集 | 分代 GC 跨代引用记录 |
| tricolor invariant | 三色不变式 | 增量/并发标记的核心不变式 |
| safepoint | 安全点 | |
| root / frame root | 根 / 帧根 | 帧根为槽粒度、非 LIFO |
| generational | 分代 | 后端 #3 |
| incremental | 增量 | 后端 #1 |
| concurrent | 并发 | 后端 #2 |
| relocating / relocation | 重定位 | 后端 #4 |
| colored pointer | 着色指针 | ZGC 传统 |
| promotion | 晋升 | 新生代对象进入老年代 |
| finalizer | 终结器 | `__del__` |
| resurrection | 复活 | 终结器使对象重新可达 |
| weak reference | 弱引用 | |
| pause | 停顿 | GC 暂停时间 |
| fragmentation | 碎片化 | |
| immortal | 不朽(对象) | `PY_FLAG_IMMORTAL` |

## 值模型

| English | 中文 | 注 |
|---|---|---|
| value class / value model | 值类 / 值模型 | 可选、无身份的载荷 |
| projection | 投影 | 语义类型到物理表示的映射 |
| value / object projection | 值投影 / 对象投影 | 同一语义类型的两种表示 |
| boxing / unboxing | 装箱 / 拆箱 | |
| boxing bridge | 装箱桥 | 两投影间的转换 |
| tagged small-int lane | 标记小整数通道 | `int` 的值投影 |
| identity escape | 身份逃逸 | 值类被取 id/is 等 |
| deopt / promote | 去优化 / 提升 | 值通道溢出的合法出路 |
| wrap | 回绕 | 值通道溢出的非法出路 |

## 自举与工程

| English | 中文 | 注 |
|---|---|---|
| bootstrap | 自举 | |
| self-hosting | 自托管 | |
| fixed point | 不动点 | pcc2/pcc3 稳定 |
| byte identity | 字节同一 | |
| stage | 阶段 | pcc0/pcc1/pcc2/pcc3 |
| gate | 闸门(gate) | 必须保持绿色的检查 |
| ratchet | 棘轮 | 只许收紧不许放松的基线 |
| baseline | 基线 | |
| claim hygiene | 声明卫生 | 每条声明标注其证明范围 |
| mode-labeled | 模式标注的 | |
| investigation | 调查 | [docs/investigations/](../../docs/investigations) 下的文档 |
| case study | 案例研究 | 本书"历史与教训"小节的体例,取材 docs/investigations |
| reproducer | 重现程序 | 最小化的失败用例 |
| regression | 回归 | |
| no-libpython | no-libpython | 不译;不依赖 CPython 运行时 |
| C kernel | C 内核 | 四层模型最底层 |
| C-API shim | C-API 垫片(shim) | 扩展所见的 ABI 面 |
