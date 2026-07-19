# Self-host miscompile family: root-fix design

## Miscompile class enumeration (from the row + investigations + memory)

| # | Class | Symptom under pcc1 | Frontend workaround today | Root-cause hypothesis |
|---|---|---|---|---|
| 1 | isinstance-cross-module | isinstance(x, Cls) false when Cls defined in another compiled module | inline tuples / structural checks | class-object identity duplicated per compiled module (module-global round-trip creates a second class object) |
| 2 | generator projection | all(gen)/any(gen) misfire | explicit loops | generator element projection lost at call boundary |
| 3 | set construction members | set(...) drops members | set->list workarounds | same family as 5 (set object built via degraded dyn path) |
| 4 | module-global data constants | module-level int/str consts read empty/zero | inline-at-use-site (memory: feedback_pcc_python_module_consts) | module attr slot zeroed in stripped .o builds — linker/section-level, distinct from 5 |
| 5 | DynType(name=...) discriminator | .name field reads "dyn" after module-global round-trip | set_typed_names side table (type_infer.py) | pcc1 runtime setattr on dataclass-like instances clobbers a NEIGHBOR slot (memory: feedback_pcc_dataclass_default_none_setattr) — string field neighbor corruption |

## Unifying suspicion

Classes 3 and 5 (and possibly 1) may share ONE runtime root: pcc1-compiled
instance field layout/setattr writing the wrong slot for dataclass-shaped
classes with default-None/late-bound fields. The documented
"default-None dataclass slot setattr clobbers a neighbor" behavior would
exactly produce: DynType.name overwritten (5), set members vanishing when the
set object's fields are neighbors (3), and class-object fields corrupted (1's
n_fields=0 family — see pcc1-tuple-unpack-self-host-str-counter-corruption).

## Plan (one sub-slice per class, self-host regression FIRST)

1. Minimal pcc1 reproducers, one file per class under tests/python/pcc1_miscompile/
   (env-gated like other pcc1 gates; each compiles a 2-module program WITH pcc1
   and asserts CPython-equal behavior).
2. Root-fix order:
   a. dataclass/default-None setattr neighbor clobber (runtime py_instance_setattr
      / field layout audit; LLDB watchpoint on the neighbor slot) — likely
      collapses 5 and 3, possibly 1.
   b. isinstance-cross-module: make compiled-module class objects canonical
      (single registry keyed by qualified name — parallels the module-object
      registry; parent-first import fix from today already reduces duplicate
      package inits that could re-create class objects).
   c. module-global consts (4): section/strip-level — audit emitted globals'
      linkage/sections in stripped .o; likely fix = used-attribute or
      no-dead-strip on module attr tables.
   d. generator projection (2): separate frontend typing slice.
3. Only after a+b prove green under the FULL bootstrap matrix, retire the
   frontend workarounds one by one (each retirement = its own commit-sized
   change gated on the pcc1 reproducers + bootstrap).
4. First-class SetType (exit criterion 3): promote set/frozenset from
   DynType(name=...) to a concrete SetType class in type_infer (identity =
   class, immune to string-field corruption), mirroring ListType/DictType.
   This retires set_typed_names entirely and is worth doing even after (a)
   fixes the corruption, because it removes the fragile discriminator class.

## Gates

- per-repro: pcc1-gated focused tests (fast, stage-1 rebuild only)
- promotion: full five-backend bootstrap matrix (the row's own 700s gate) —
  run once at the end with the goal's final validation.

## Repro log (2026-07-18, during full-suite wait)

- NEGATIVE: a minimal 3-field class (`kind`, `name=None`, `extra=None`) with a
  post-construction `t.name = "set"` setattr compiles clean under host pcc0
  `--backend self --python-libpython=off` (default runtime tier): output
  byte-identical to CPython (`dyn set None` / `obj x payload`). So the
  neighbor-clobber needs a fuller shape — candidates: many-field Type-like
  classes (pcc's Type hierarchy has 5+ fields), module-global instance
  round-trips through py_module_attr tables, or the pcc1 (stage-1-compiled)
  binary rather than a host-pcc0 artifact. Next repro iteration should mirror
  type_infer's actual shapes: a module-global registry dict of Type instances
  mutated across module boundaries, compiled as a 2-module program.
- NEGATIVE v2: two-module registry shape (5-field Ty class, module-global
  REGISTRY dict + interned module-global instance, cross-module attr reads and
  a re-intern call) compiled BY pcc1 with PCC_PACKAGE_SITE sibling resolution:
  output byte-identical to CPython. Conclusion: the family does not reduce to
  these small shapes; per the set investigation's method note, the next step
  is env-gated instrumentation INSIDE pcc1's own inference (rebuild stage1
  with prints) rather than external mini-programs. Design stands; repro work
  is the first sub-slice's task.
