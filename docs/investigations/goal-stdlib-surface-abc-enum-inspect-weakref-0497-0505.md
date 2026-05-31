# goal native stdlib surface: abc / enum / inspect / weakref

This pack extends pcc's native-compilable stdlib surface to reduce fallback
pressure in self-host and pure-Python package tests.

## abc

- `ABCMeta`
- `ABC`
- abstract decorators
- virtual subclass register
- cache token

## enum

- `Enum`
- `IntEnum`
- `auto`
- `unique`
- iteration and value lookup

## inspect

- `signature`
- `Parameter`
- `Signature`
- common predicates
- `getmembers`
- `getdoc`
- `unwrap`

## weakref

- `ref`
- `proxy`
- `WeakValueDictionary`
- `WeakKeyDictionary`
- `WeakSet`
- `finalize`

The weakref Python shim is a bootstrap-compatible approximation.  Compiled
runtime code still has C-level weakref support.

Gate:

```bash
bash scripts/run_stdlib_surface_goal_gate.sh
```
