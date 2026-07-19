# pcc package model: acquire / build / run 三层契约

Status: ACTIVE design contract (2026-07-18). Governs `pcc -m pip`, the package
executor, and the pcc-native extension ladder (`PKG-P1-NATIVE-EXTENSION-LADDER`).

## 问题

"支持 pip" 在讨论里混掉了三件完全不同的事,导致范围与声明反复混乱:

```text
A 获取 acquire   从 PyPI 下载源码/wheel,解析依赖与版本(网络 + resolver + build isolation)
B 构建 build     把源码/工件变成 pcc 能跑的产物(pcc-native ABI 对 pcc 的 C-API shim 编 C 扩展)
C 运行 run       在 pcc1 的 no-libpython 运行时里 module-graph + dlopen 扩展 + 对象模型
```

## 契约

**pcc 拥有 B 和 C;A 委托给宿主工具(pip/uv),永不自造。**

理由(与北极星逐条对齐):

1. pcc 的论题是"拥有**执行**",不是"拥有包管理器"。B/C 是使命与差异化;A 是
   CPython 生态已解决的问题,复制它零收益。
2. 真 pip 的网络栈 + 依赖解析器 + build isolation 是动态/反射重的 Python 代码,
   拉进 pcc1 的 no-libpython 闭世界必须开 CPython 桥——违背 no-libpython。
3. 生态义务(AGENTS.md 义务 3)要求修**可复用机制**(install/import/ABI/buffer/
   capsule/build-surface),不要求成为 PyPI 客户端。

因此 **pcc1 替换的是 Python 的执行环境,不是 Python 的包分发工具链。**

## `pcc -m pip` 的明确行为(三种入参)

| 入参形态 | 行为 |
|---|---|
| 本地源码树 / 本地 wheel / `--find-links <本地目录>` 可解析的名字 | 按 `--abi`(默认 `pcc-native`)构建 + 安装进 `--target` site |
| `--dry-run` / `--report` | 只报告计划,不安装 |
| 裸包名且本地不可解析(需要联网获取) | **明确失败**(`ok:false`, exit 2),并输出 `acquire_hint`:先用宿主工具下载(`python3 -m pip download <pkg> -d ./wheels`),再 `pcc -m pip install <pkg> --find-links ./wheels`。绝不假装支持、绝不静默空转 |

`--index-url` 只作为本地 resolver 的目录来源接受;任何真实网络获取都路由到
acquire_hint 失败路径。

## ABI 面(不变,重申)

- `pcc-native`(默认):对 pcc 的窄 `Python.h`/C-API shim 构建,不链 libpython;
  CPython-ABI 工件被 `PCC-PKG-004` 拒绝。
- `cpython-compat` / `libpython`:显式兼容模式,链 libpython;是**独立声明**,
  不得与 pcc-native 结论互相顶替(§0.10)。

## 端到端阶梯(把分散证据缝成一条)

现状零件齐但分散:install 能从本地源做 pcc-native 构建(`install.py` →
`build_exec` meson 重放 + include 重定向);构建 gate
(`scripts/numpy_package_artifact_gate.py`)是同一执行器的聚焦调用;L4/L5 从
预制 site 证明 import + 数组运行。**缺一条单命令端到端证明:**

```text
pcc -m pip install <本地真实源> --abi pcc-native --target <site>
  -> site 含 pcc-native 扩展 + Python 模块
  -> pcc1 --backend self --python-libpython=off 编 import <pkg> 程序
  -> 运行,断言输出,otool 无 libpython
```

第一根阶梯用 NumPy(最接近);第二根用一个纯 Python 包或小 C 扩展证明机制
通用。gate 形态沿用 L4/L5:`pytest.mark.integration` + 环境变量 opt-in,
工具链/源缺失报 skip 不报成功。**禁止 `if package == "numpy"`。**

## 声明卫生(始终分开)

`pcc-native import X 跑通` ≠ `pip install X`(本地)成功 ≠ `cpython-compat 装 X`
≠ "支持 PyPI"。四者证据独立,永不合并。

## 非目标

- 不实现 PyPI 网络客户端、TLS、上游依赖 resolver、build isolation。
- 不追求"任意 PyPI wheel 直接可装可跑"。
- 不为让某包过 gate 加包名特判。
