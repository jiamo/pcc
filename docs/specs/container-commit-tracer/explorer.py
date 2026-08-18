#!/usr/bin/env python3
"""Exhaustive explicit-state exploration of ContainerCommitTracer.tla.

Temporary scaffolding while no working TLC artifact is available (the only
local tla2tools jar NPEs parsing any module; release downloads are blocked
in this environment).  Mirrors the TLA+ Next relation action by action and
checks the same invariants on every reachable state at identical bounds.
"""
import sys

VALUES = ("v1", "v2", "v3", "v4", "v5", "v6")
NONE = None


def run(max_ops, trace_budget, victim_swap_bug, early_plan_bug):
    init = ((False, "committed", NONE, NONE),
            frozenset(), frozenset(), frozenset(), frozenset(),
            "none", max_ops, trace_budget)

    def check(s):
        """Return violated invariant name(s) for a state, mirroring the
        TLA+ invariant definitions."""
        entry, held, garbage, pd, fin, obs, ops, tr = s
        occ, phase, val, new = entry
        bad = []
        if occ and val in fin:
            bad.append("InvNoPrematureFree")
        if held & fin:
            bad.append("InvNoPrematureFree(held)")
        if obs not in ("none", "committed"):
            bad.append("InvPlanCompletionSeesCommitted")
        return bad

    def succ(st):
        entry, held, garbage, pd, fin, obs, ops, tr = st
        occ, phase, val, new = entry
        out = []

        def push(e=entry, h=held, g=garbage, p=pd, f=fin,
                 o=obs, op=ops, t=tr):
            out.append((e, h, g, p, f, o, op, t))

        # StartInsert(v): fresh allocation only
        if ops > 0 and (not occ) and phase == "committed":
            for v in VALUES:
                if v in garbage or v in fin or v in held \
                        or v == val:
                    continue
                push(e=(False, "half", v, NONE), h=held | {v})
        # CommitInsert(v): requires matching rooted insert window
        if phase == "half" and (not occ) and val in VALUES and val in held \
                and ops > 0:
            push(e=(True, "committed", val, NONE),
                 h=held - {val}, op=ops - 1)
        # StartReplace(v): fresh allocation only
        if ops > 0 and occ and phase == "committed":
            for v in VALUES:
                if v in garbage or v in fin or v in held \
                        or v == val:
                    continue
                push(e=(True, "half", val, v), h=held | {v})
        # CommitReplace
        if phase == "half" and occ and new in VALUES and new in held \
                and ops > 0:
            if victim_swap_bug:
                push(e=(True, "committed", new, NONE),
                     h=held - {new}, g=garbage | {new}, op=ops - 1)
            else:
                push(e=(True, "committed", new, NONE),
                     h=held - {new}, g=garbage | {val}, op=ops - 1)
        # StartDelete
        if ops > 0 and occ and phase == "committed":
            push(e=(True, "deleting", val, NONE))
        # CommitDelete
        if occ and phase == "deleting" and ops > 0:
            push(e=(False, "committed", NONE, NONE),
                 g=garbage | {val}, op=ops - 1)
        # FinishPlan(v)
        for v in garbage - pd:
            if early_plan_bug or phase == "committed":
                push(p=pd | {v},
                     o="committed" if phase == "committed" else phase)
        # TraceStep
        if tr > 0:
            push(t=tr - 1)
        # SweepCollect(v)
        for v in pd - fin:
            push(f=fin | {v})
        # Done: explicit quiescent self-loop so TLC does not report bounded
        # completion as a protocol deadlock.
        if ops == 0 and tr == 0 and phase == "committed" \
                and not held and pd == garbage and fin == pd:
            push()
        return out

    seen = {init}
    frontier = [init]
    violations = []
    if check(init):
        violations.append((check(init), init))
    while frontier:
        nxt = []
        for st in frontier:
            for s in succ(st):
                bad = check(s)
                if bad:
                    key = (tuple(bad), s)
                    if key not in violations:
                        violations.append(key)
                if s not in seen:
                    seen.add(s)
                    nxt.append(s)
        frontier = nxt
    return len(seen), violations


if __name__ == "__main__":
    configs = [
        ("clean", dict(victim_swap_bug=False, early_plan_bug=False)),
        ("victim_swap_bug", dict(victim_swap_bug=True, early_plan_bug=False)),
        ("early_plan_bug", dict(victim_swap_bug=False, early_plan_bug=True)),
    ]
    rc = 0
    for name, kw in configs:
        n, viol = run(max_ops=4, trace_budget=2, **kw)
        print(f"[{name}] reachable states: {n}")
        if viol:
            rc = 1
            kinds = sorted({k for ks, _ in viol for k in ks})
            print(f"[{name}] INVARIANT VIOLATIONS ({kinds})")
            print(f"  first counterexample state: {viol[0][1]}")
        else:
            print(f"[{name}] all invariants hold")
    sys.exit(rc)
