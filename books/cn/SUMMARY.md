# 《pcc 的设计与实现》目录

- [前言](ch00-preface.md)

## 第 I 部 总览

- [第 1 章 导论:拥有 Python 的执行](ch01-introduction.md)
- [第 2 章 体系结构总览](ch02-architecture.md)

## 第 II 部 C 前端

- [第 3 章 C 前端:解析、伪 libc 与求值器](ch03-c-frontend.md)
- [第 4 章 C 语义低层化与符号性](ch04-c-lowering-signedness.md)

## 第 III 部 Python 前端

- [第 5 章 类型化 Python 前端](ch05-typed-python-frontend.md)
- [第 6 章 Python 低层化:facade 与 mixin 群](ch06-python-lowering.md)

## 第 IV 部 运行时

- [第 7 章 对象模型](ch07-object-model.md)
- [第 8 章 异常模型](ch08-exception-model.md)
- [第 9 章 引用计数与所有权](ch09-refcount-ownership.md)

## 第 V 部 垃圾收集:五后端实验室

- [第 10 章 五 GC 架构与平等契约](ch10-gc-architecture.md)
- [第 11 章 五个后端:从引用计数到重定位](ch11-gc-backends.md)

## 第 VI 部 后端与链接

- [第 12 章 LLVM 后端与 llvm_capi 对齐](ch12-llvm-backends.md)
- [第 13 章 self 后端:没有 LLVM 的原生发射](ch13-self-backend.md)

## 第 VII 部 自举与 no-libpython

- [第 14 章 no-libpython 与 zero-libc:让运行时成为 pcc-Python](ch14-no-libpython.md)
- [第 15 章 自举:pcc1→pcc2→pcc3 不动点](ch15-bootstrap-fixed-point.md)

## 第 VIII 部 值模型与生态

- [第 16 章 值模型:投影而非定宽](ch16-value-model.md)
- [第 17 章 包、C-API shim 与扩展 ABI](ch17-packages-capi.md)

## 第 IX 部 工程方法

- [第 18 章 工程方法论:测试、调查与声明卫生](ch18-engineering-method.md)

## 第 X 部 加速器

- [第 19 章 GPU 内核 IR、Metal 与加速器执行](ch19-gpu-kernel-ir.md)

## 第 XI 部 应用执行

- [第 20 章 声明式 GUI:组件、调度与无 WebView 应用边界](ch20-declarative-gui.md)

## 附录

- [附录 A 仓库地图](appendix-a-repo-map.md)
- [附录 B 术语对照表](appendix-b-glossary.md)
