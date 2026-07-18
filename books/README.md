# The Design and Implementation of pcc / 《pcc 的设计与实现》

A book about pcc, written against the real repository: design rationale,
mechanism grounded in actual source files and identifiers, and war stories
drawn from `docs/investigations/`. Two editions, same author voice:

- `cn/` — 中文版(主笔语言)。目录见 [cn/SUMMARY.md](cn/SUMMARY.md)。
- `en/` — English edition (a native rewrite, not a literal translation).
  Contents: [en/SUMMARY.md](en/SUMMARY.md).

Editorial machinery:

- [PLAN.md](PLAN.md) — the chapter blueprint (scope, sources, required
  questions, war-story leads per chapter).
- [STYLE.md](STYLE.md) — the style contract both editions follow: design
  rationale before mechanism, every claim grounded in real code, claim
  hygiene (mode-labeled statements, honest open problems), fixed Chinese
  terminology, identical chapter structure.

Conventions: source references use file path + identifier name (never line
numbers); code identifiers, CLI flags, and environment variables appear
verbatim. The book corresponds to the repository state of June 2026; where
book and code disagree, the code wins.
