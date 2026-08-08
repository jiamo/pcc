from __future__ import annotations

"""Fail-closed verifier for the parsed self-backend LLVM-IR subset.

The textual parser deliberately accepts only a finite LLVM-IR surface, but a
successfully decoded instruction is not by itself proof that its SSA graph is
valid.  This verifier runs before stack-slot assignment or target lowering and
checks the invariants that the emitters otherwise have to assume.

It is diagnostics-only: it never rewrites IR, repairs PHIs, or widens the
accepted instruction set.  Check names are stable so a malformed program is
reported at this boundary instead of becoming a target-specific miscompile.
"""

from dataclasses import dataclass

from . import BackendUnavailable
from .self_backend_analysis import (
    instruction_defined_value,
    instruction_used_values,
    is_local_value_ref,
    terminator_used_values,
)
from .self_backend_ir import (
    I1,
    ParsedBlock,
    ParsedFunction,
    ParsedInstr,
    ParsedModule,
    TypeDesc,
    text_key_names_equal,
)


@dataclass(frozen=True)
class _Definition:
    name: str
    type: TypeDesc
    # -1 denotes a function argument, which dominates every reachable block.
    block_index: int
    # PHIs use -1; ordinary instructions use their index inside the block.
    position: int


def _stable_text_key(text: str) -> int:
    """Hash text without relying on the native-bootstrap string hash cache."""

    modulus = 1099511627776
    value = 0
    index = 0
    while index < len(text):
        value = (value * 131 + ord(text[index])) % modulus
        index += 1
    return value


def _fail(check: str, func: ParsedFunction, detail: str) -> None:
    raise BackendUnavailable(
        f"self IR verifier [{check}] in {func.name!r}: {detail}"
    )


def _types_match(left: TypeDesc, right: TypeDesc) -> bool:
    # Opaque and typed LLVM pointers share one machine representation in the
    # current subset.  The parser intentionally retains a pointee when one was
    # written, but that spelling is not an operand mismatch under opaque ptr.
    if left.is_ptr and right.is_ptr:
        return True
    if left.kind != right.kind:
        return False
    if left.is_void:
        return True
    if left.is_int or left.is_fp:
        return left.width == right.width
    if left.is_array:
        return (
            left.count == right.count
            and left.elem is not None
            and right.elem is not None
            and _types_match(left.elem, right.elem)
        )
    if left.is_struct:
        if left.name or right.name:
            return left.name == right.name
        return len(left.fields) == len(right.fields) and all(
            _types_match(left_field, right_field)
            for left_field, right_field in zip(left.fields, right.fields)
        )
    return False


def _is_i1(value_type: TypeDesc) -> bool:
    return value_type.is_int and value_type.width == 1


def _is_int(value_type: TypeDesc) -> bool:
    return value_type.is_int


def _is_int_lane(value_type: TypeDesc) -> bool:
    return value_type.is_int or (
        value_type.is_array
        and value_type.elem is not None
        and value_type.elem.is_int
    )


def _is_fp_lane(value_type: TypeDesc) -> bool:
    return value_type.is_fp or (
        value_type.is_array
        and value_type.elem is not None
        and value_type.elem.is_fp
    )


def _block_index(
    blocks: list[ParsedBlock],
    buckets: dict[int, list[int]],
    name: str,
) -> int:
    for index in buckets.get(_stable_text_key(name), []):
        if text_key_names_equal(blocks[index].name, name):
            return index
    return -1


def _definition_get(
    buckets: dict[int, list[_Definition]], name: str
) -> _Definition | None:
    for definition in buckets.get(_stable_text_key(name), []):
        if text_key_names_equal(definition.name, name):
            return definition
    return None


def _record_definition(
    func: ParsedFunction,
    buckets: dict[int, list[_Definition]],
    definition: _Definition,
) -> None:
    bucket = buckets.setdefault(_stable_text_key(definition.name), [])
    for existing in bucket:
        if text_key_names_equal(existing.name, definition.name):
            _fail(
                "ssa-definition",
                func,
                f"value {definition.name!r} has more than one definition",
            )
    bucket.append(definition)


def _instruction_result_type(instr: ParsedInstr) -> TypeDesc | None:
    kind = instr.kind
    data = instr.data
    if kind == "alloca":
        return data[1].ptr()
    if kind in {"load", "load_atomic"}:
        return data[1]
    if kind == "atomicrmw":
        return data[4]
    if kind == "cmpxchg":
        return data[1]
    if kind == "syscall6":
        return TypeDesc("int", 64)
    if kind in {"binop", "fbinop"}:
        return data[2]
    if kind == "fneg":
        return data[1]
    if kind in {"icmp", "fcmp"}:
        value_type = data[2]
        if value_type.is_array and value_type.elem is not None:
            return TypeDesc("array", count=value_type.count, elem=I1)
        return I1
    if kind == "cast":
        return data[4]
    if kind in {"select", "freeze"}:
        return data[1]
    if kind in {"insertelement", "shufflevector"}:
        return data[1]
    if kind == "extractelement":
        return data[4]
    if kind == "extractvalue":
        return data[4]
    if kind == "insertvalue":
        return data[1]
    if kind == "va_arg":
        return data[3]
    if kind == "gep":
        return data[2]
    if kind == "call":
        return data[1]
    return None


def _successor_names(term: ParsedInstr) -> tuple[str, ...]:
    if term.kind == "br":
        return (term.data[0],)
    if term.kind == "br_cond":
        return (term.data[1], term.data[2])
    if term.kind == "switch":
        return (
            term.data[2],
            *(target for _case_value, target in term.data[3]),
        )
    return ()


def _build_cfg(
    func: ParsedFunction,
) -> tuple[dict[int, list[int]], list[list[int]], list[list[int]]]:
    blocks = func.blocks
    block_buckets: dict[int, list[int]] = {}
    for index, block in enumerate(blocks):
        bucket = block_buckets.setdefault(_stable_text_key(block.name), [])
        for existing_index in bucket:
            if text_key_names_equal(blocks[existing_index].name, block.name):
                _fail(
                    "terminator",
                    func,
                    f"duplicate basic block label {block.name!r}",
                )
        bucket.append(index)

    predecessors: list[list[int]] = [[] for _block in blocks]
    successors: list[list[int]] = [[] for _block in blocks]
    for index, block in enumerate(blocks):
        term = block.terminator
        if term is None:
            _fail(
                "terminator",
                func,
                f"block {block.name!r} has no terminator",
            )
        for target_name in _successor_names(term):
            target_index = _block_index(blocks, block_buckets, target_name)
            if target_index < 0:
                _fail(
                    "terminator",
                    func,
                    f"block {block.name!r} branches to missing block {target_name!r}",
                )
            if target_index not in successors[index]:
                successors[index].append(target_index)
            if index not in predecessors[target_index]:
                predecessors[target_index].append(index)
    return block_buckets, predecessors, successors


def _compute_dominators(
    predecessors: list[list[int]],
    successors: list[list[int]],
) -> tuple[list[int], list[int]]:
    """Dominator-tree preorder intervals via Cooper-Harvey-Kennedy idoms.

    Returns (dom_in, dom_out); block d dominates block b iff
    dom_in[d] <= dom_in[b] <= dom_out[d]. Memory stays O(blocks): dense
    per-block dominator sets are O(blocks^2), and a 72k-block generated
    module top needs hundreds of GiB that way.
    """
    block_count = len(predecessors)
    postorder: list[int] = []
    visited = [False] * block_count
    visited[0] = True
    stack: list[tuple[int, int]] = [(0, 0)]
    while stack:
        block, edge = stack[-1]
        if edge < len(successors[block]):
            stack[-1] = (block, edge + 1)
            target = successors[block][edge]
            if not visited[target]:
                visited[target] = True
                stack.append((target, 0))
        else:
            stack.pop()
            postorder.append(block)
    rpo: list[int] = []
    index = len(postorder) - 1
    while index >= 0:
        rpo.append(postorder[index])
        index -= 1
    rpo_number = [-1] * block_count
    for order, block in enumerate(rpo):
        rpo_number[block] = order
    idom = [-1] * block_count
    idom[0] = 0
    changed = True
    while changed:
        changed = False
        for block in rpo:
            if block == 0:
                continue
            new_idom = -1
            for pred in predecessors[block]:
                if idom[pred] < 0:
                    continue
                if new_idom < 0:
                    new_idom = pred
                    continue
                finger1 = pred
                finger2 = new_idom
                while finger1 != finger2:
                    while rpo_number[finger1] > rpo_number[finger2]:
                        finger1 = idom[finger1]
                    while rpo_number[finger2] > rpo_number[finger1]:
                        finger2 = idom[finger2]
                new_idom = finger1
            if new_idom >= 0 and idom[block] != new_idom:
                idom[block] = new_idom
                changed = True
    children: list[list[int]] = [[] for _index in range(block_count)]
    for block in rpo:
        if block != 0 and idom[block] >= 0:
            children[idom[block]].append(block)
    dom_in = [-1] * block_count
    dom_out = [-1] * block_count
    counter = 1
    dom_in[0] = 0
    walk: list[tuple[int, int]] = [(0, 0)]
    while walk:
        block, child = walk[-1]
        if child < len(children[block]):
            walk[-1] = (block, child + 1)
            target = children[block][child]
            dom_in[target] = counter
            counter += 1
            walk.append((target, 0))
        else:
            walk.pop()
            dom_out[block] = counter
            counter += 1
    # parse_self_backend_module filters unreachable blocks, so only malformed
    # input still contains one; such a block dominates only itself.
    for block in range(block_count):
        if dom_in[block] < 0:
            dom_in[block] = counter
            dom_out[block] = counter + 1
            counter += 2
    return dom_in, dom_out


def _block_dominates(
    dominators: tuple[list[int], list[int]],
    dom_block: int,
    block: int,
) -> bool:
    dom_in, dom_out = dominators
    return dom_in[dom_block] <= dom_in[block] <= dom_out[dom_block]


def _build_definitions(
    func: ParsedFunction,
) -> dict[int, list[_Definition]]:
    definitions: dict[int, list[_Definition]] = {}
    for arg in func.args:
        _record_definition(
            func,
            definitions,
            _Definition(arg.name, arg.type, -1, -1),
        )
    for block_index, block in enumerate(func.blocks):
        for phi in block.phis:
            _record_definition(
                func,
                definitions,
                _Definition(phi.dest, phi.type, block_index, -1),
            )
        for position, instr in enumerate(block.instructions):
            dest = instruction_defined_value(instr)
            if dest is None:
                continue
            result_type = _instruction_result_type(instr)
            if result_type is None or result_type.is_void:
                _fail(
                    "operand-type",
                    func,
                    f"instruction {instr.kind!r} defines {dest!r} without a value type",
                )
            _record_definition(
                func,
                definitions,
                _Definition(dest, result_type, block_index, position),
            )
    return definitions


def _require_local_type(
    func: ParsedFunction,
    definitions: dict[int, list[_Definition]],
    value: str,
    expected: TypeDesc,
    *,
    context: str,
) -> None:
    if not is_local_value_ref(value):
        return
    definition = _definition_get(definitions, value)
    if definition is None:
        _fail("ssa-dominance", func, f"{context} uses undefined value {value!r}")
    if not _types_match(definition.type, expected):
        _fail(
            "operand-type",
            func,
            f"{context} expects {expected.describe()} for {value!r}, got "
            + definition.type.describe(),
        )


def _require_local_int(
    func: ParsedFunction,
    definitions: dict[int, list[_Definition]],
    value: str,
    *,
    context: str,
) -> None:
    if not is_local_value_ref(value):
        return
    definition = _definition_get(definitions, value)
    if definition is None:
        _fail("ssa-dominance", func, f"{context} uses undefined value {value!r}")
    if not _is_int(definition.type):
        _fail(
            "operand-type",
            func,
            f"{context} expects an integer for {value!r}, got "
            + definition.type.describe(),
        )


def _verify_instruction_types(
    func: ParsedFunction,
    definitions: dict[int, list[_Definition]],
    block: ParsedBlock,
    instr: ParsedInstr,
) -> None:
    kind = instr.kind
    data = instr.data
    context = f"{block.name!r}/{kind}"
    if kind == "store":
        value_type, value, ptr_type, ptr = data
        if not ptr_type.is_ptr:
            _fail("operand-type", func, f"{context} address type is not a pointer")
        _require_local_type(func, definitions, value, value_type, context=context)
        _require_local_type(func, definitions, ptr, ptr_type, context=context)
    elif kind in {"load", "load_atomic"}:
        _dest, _value_type, ptr_type, ptr, *_rest = data
        if not ptr_type.is_ptr:
            _fail("operand-type", func, f"{context} address type is not a pointer")
        _require_local_type(func, definitions, ptr, ptr_type, context=context)
    elif kind == "store_atomic":
        value_type, value, ptr_type, ptr, _ordering = data
        if not ptr_type.is_ptr:
            _fail("operand-type", func, f"{context} address type is not a pointer")
        _require_local_type(func, definitions, value, value_type, context=context)
        _require_local_type(func, definitions, ptr, ptr_type, context=context)
    elif kind == "atomicrmw":
        _dest, _op, ptr_type, ptr, value_type, value, _ordering = data
        if not ptr_type.is_ptr:
            _fail("operand-type", func, f"{context} address type is not a pointer")
        _require_local_type(func, definitions, ptr, ptr_type, context=context)
        _require_local_type(func, definitions, value, value_type, context=context)
    elif kind == "cmpxchg":
        (
            _dest,
            _pair_type,
            ptr_type,
            ptr,
            value_type,
            expected,
            desired,
            _success,
            _failure,
        ) = data
        if not ptr_type.is_ptr:
            _fail("operand-type", func, f"{context} address type is not a pointer")
        _require_local_type(func, definitions, ptr, ptr_type, context=context)
        _require_local_type(func, definitions, expected, value_type, context=context)
        _require_local_type(func, definitions, desired, value_type, context=context)
    elif kind == "syscall6":
        _dest, args = data
        i64 = TypeDesc("int", 64)
        for value in args:
            _require_local_type(func, definitions, value, i64, context=context)
    elif kind == "va_arg":
        _dest, ap_type, ap, _value_type = data
        if not ap_type.is_ptr:
            _fail("operand-type", func, f"{context} va_list type is not a pointer")
        _require_local_type(func, definitions, ap, ap_type, context=context)
    elif kind in {"binop", "fbinop", "icmp", "fcmp"}:
        _op, _dest, value_type, lhs, rhs = data
        if kind == "binop" and not _is_int_lane(value_type):
            _fail("operand-type", func, f"{context} expects integer operands")
        if kind in {"fbinop", "fcmp"} and not _is_fp_lane(value_type):
            _fail("operand-type", func, f"{context} expects floating operands")
        if kind == "icmp" and not (
            _is_int_lane(value_type) or value_type.is_ptr
        ):
            _fail("operand-type", func, f"{context} expects integer/pointer operands")
        _require_local_type(func, definitions, lhs, value_type, context=context)
        _require_local_type(func, definitions, rhs, value_type, context=context)
    elif kind == "fneg":
        _dest, value_type, value = data
        if not _is_fp_lane(value_type):
            _fail("operand-type", func, f"{context} expects a floating operand")
        _require_local_type(func, definitions, value, value_type, context=context)
    elif kind == "cast":
        _op, _dest, src_type, value, _dst_type = data
        _require_local_type(func, definitions, value, src_type, context=context)
    elif kind == "select":
        _dest, value_type, cond, true_value, false_value = data
        if is_local_value_ref(cond):
            cond_def = _definition_get(definitions, cond)
            if cond_def is None:
                _fail("ssa-dominance", func, f"{context} uses undefined value {cond!r}")
            cond_type = cond_def.type
            vector_i1 = (
                cond_type.is_array
                and cond_type.elem is not None
                and _is_i1(cond_type.elem)
                and value_type.is_array
                and cond_type.count == value_type.count
            )
            if not (_is_i1(cond_type) or vector_i1):
                _fail(
                    "operand-type",
                    func,
                    f"{context} condition {cond!r} is not i1/vector-i1",
                )
        _require_local_type(func, definitions, true_value, value_type, context=context)
        _require_local_type(func, definitions, false_value, value_type, context=context)
    elif kind == "freeze":
        _dest, value_type, value = data
        _require_local_type(func, definitions, value, value_type, context=context)
    elif kind == "insertelement":
        _dest, vector_type, vector, elem_type, elem, index = data
        _require_local_type(func, definitions, vector, vector_type, context=context)
        _require_local_type(func, definitions, elem, elem_type, context=context)
        _require_local_int(func, definitions, index, context=context)
    elif kind == "extractelement":
        _dest, vector_type, vector, index, _elem_type = data
        _require_local_type(func, definitions, vector, vector_type, context=context)
        _require_local_int(func, definitions, index, context=context)
    elif kind == "shufflevector":
        _dest, result_type, lhs, rhs, mask_type, mask = data
        for value in (lhs, rhs):
            if not is_local_value_ref(value):
                continue
            definition = _definition_get(definitions, value)
            if definition is None:
                _fail("ssa-dominance", func, f"{context} uses undefined value {value!r}")
            if (
                not definition.type.is_array
                or definition.type.elem is None
                or result_type.elem is None
                or not _types_match(definition.type.elem, result_type.elem)
            ):
                _fail("operand-type", func, f"{context} has incompatible vector operand")
        _require_local_type(func, definitions, mask, mask_type, context=context)
    elif kind == "extractvalue":
        _dest, aggregate_type, value, _indices, _result_type, _offset = data
        _require_local_type(func, definitions, value, aggregate_type, context=context)
    elif kind == "insertvalue":
        (
            _dest,
            aggregate_type,
            aggregate_value,
            elem_type,
            elem_value,
            _indices,
            _offset,
        ) = data
        _require_local_type(
            func, definitions, aggregate_value, aggregate_type, context=context
        )
        _require_local_type(func, definitions, elem_value, elem_type, context=context)
    elif kind == "gep":
        _dest, _base_type, ptr_type, ptr, indices = data
        if not ptr_type.is_ptr:
            _fail("operand-type", func, f"{context} base type is not a pointer")
        _require_local_type(func, definitions, ptr, ptr_type, context=context)
        for index_type, index_value in indices:
            _require_local_type(
                func, definitions, index_value, index_type, context=context
            )
    elif kind == "call":
        (
            _dest,
            _ret_type,
            callee,
            is_indirect,
            args,
            fixed_arg_count,
            is_vararg,
            arg_alignments,
        ) = data
        if len(arg_alignments) != len(args):
            _fail(
                "operand-type",
                func,
                f"{context} call argument alignment count does not match operands",
            )
        for alignment in arg_alignments:
            if alignment < 0 or (alignment and alignment & (alignment - 1)):
                _fail(
                    "operand-type",
                    func,
                    f"{context} call argument has invalid alignment {alignment}",
                )
        if is_indirect:
            _require_local_type(
                func,
                definitions,
                callee,
                TypeDesc("ptr", pointee=TypeDesc("void")),
                context=context,
            )
        if is_vararg and len(args) < fixed_arg_count:
            _fail("operand-type", func, f"{context} has too few fixed arguments")
        if fixed_arg_count and not is_vararg and len(args) != fixed_arg_count:
            _fail("operand-type", func, f"{context} argument count disagrees with signature")
        for arg_type, arg_value in args:
            _require_local_type(
                func, definitions, arg_value, arg_type, context=context
            )


def _definition_dominates_use(
    definition: _Definition,
    block_index: int,
    position: int,
    dominators: tuple[list[int], list[int]],
) -> bool:
    if definition.block_index < 0:
        return True
    if definition.block_index == block_index:
        return definition.position < position
    return _block_dominates(dominators, definition.block_index, block_index)


def _verify_ordinary_uses(
    func: ParsedFunction,
    definitions: dict[int, list[_Definition]],
    dominators: tuple[list[int], list[int]],
) -> None:
    for block_index, block in enumerate(func.blocks):
        for position, instr in enumerate(block.instructions):
            _verify_instruction_types(func, definitions, block, instr)
            for value in instruction_used_values(instr):
                definition = _definition_get(definitions, value)
                if definition is None:
                    _fail(
                        "ssa-dominance",
                        func,
                        f"{block.name!r}/{instr.kind} uses undefined value {value!r}",
                    )
                if not _definition_dominates_use(
                    definition, block_index, position, dominators
                ):
                    _fail(
                        "ssa-dominance",
                        func,
                        f"definition of {value!r} does not dominate "
                        f"{block.name!r}/{instr.kind}",
                    )
        term = block.terminator
        assert term is not None
        term_position = len(block.instructions)
        for value in terminator_used_values(term):
            definition = _definition_get(definitions, value)
            if definition is None:
                _fail(
                    "ssa-dominance",
                    func,
                    f"terminator in {block.name!r} uses undefined value {value!r}",
                )
            if not _definition_dominates_use(
                definition, block_index, term_position, dominators
            ):
                _fail(
                    "ssa-dominance",
                    func,
                    f"definition of {value!r} does not dominate terminator "
                    f"in {block.name!r}",
                )


def _verify_phis(
    func: ParsedFunction,
    definitions: dict[int, list[_Definition]],
    predecessors: list[list[int]],
    dominators: tuple[list[int], list[int]],
    block_buckets: dict[int, list[int]],
) -> None:
    blocks = func.blocks
    for block_index, block in enumerate(blocks):
        expected = set(predecessors[block_index])
        for phi in block.phis:
            if not expected:
                _fail(
                    "phi-predecessors",
                    func,
                    f"phi {phi.dest!r} appears in predecessor-free block {block.name!r}",
                )
            actual: set[int] = set()
            for incoming in phi.incoming:
                pred_index = _block_index(blocks, block_buckets, incoming.label)
                if pred_index < 0 or pred_index not in expected:
                    _fail(
                        "phi-predecessors",
                        func,
                        f"phi {phi.dest!r} in {block.name!r} names non-predecessor "
                        f"{incoming.label!r}",
                    )
                if pred_index in actual:
                    _fail(
                        "phi-predecessors",
                        func,
                        f"phi {phi.dest!r} in {block.name!r} repeats predecessor "
                        f"{incoming.label!r}",
                    )
                actual.add(pred_index)
                if not is_local_value_ref(incoming.value):
                    continue
                definition = _definition_get(definitions, incoming.value)
                if definition is None:
                    _fail(
                        "ssa-dominance",
                        func,
                        f"phi {phi.dest!r} uses undefined value {incoming.value!r}",
                    )
                if not _types_match(definition.type, phi.type):
                    _fail(
                        "operand-type",
                        func,
                        f"phi {phi.dest!r} expects {phi.type.describe()} for "
                        f"{incoming.value!r}, got {definition.type.describe()}",
                    )
                # A PHI use occurs on the incoming predecessor edge.  A local
                # definition in that predecessor is before its terminator;
                # otherwise the defining block must dominate the predecessor.
                if definition.block_index >= 0 and not (
                    definition.block_index == pred_index
                    or _block_dominates(
                        dominators, definition.block_index, pred_index
                    )
                ):
                    _fail(
                        "ssa-dominance",
                        func,
                        f"definition of {incoming.value!r} does not dominate "
                        f"phi edge {incoming.label!r} -> {block.name!r}",
                    )
            if actual != expected:
                missing = [
                    blocks[index].name for index in sorted(expected - actual)
                ]
                _fail(
                    "phi-predecessors",
                    func,
                    f"phi {phi.dest!r} in {block.name!r} is missing "
                    f"predecessors {missing!r}",
                )


def _verify_terminator_types(
    func: ParsedFunction,
    definitions: dict[int, list[_Definition]],
) -> None:
    for block in func.blocks:
        term = block.terminator
        assert term is not None
        context = f"terminator in {block.name!r}"
        if term.kind == "ret_void":
            if not func.ret_type.is_void:
                _fail("terminator", func, f"{context} returns void from non-void function")
        elif term.kind == "ret":
            ret_type, value = term.data
            if not _types_match(ret_type, func.ret_type):
                _fail(
                    "terminator",
                    func,
                    f"{context} returns {ret_type.describe()}, function returns "
                    + func.ret_type.describe(),
                )
            _require_local_type(
                func, definitions, value, ret_type, context=context
            )
        elif term.kind == "br_cond":
            cond = term.data[0]
            _require_local_type(func, definitions, cond, I1, context=context)
        elif term.kind == "switch":
            value_type, value, _default, cases = term.data
            _require_local_type(
                func, definitions, value, value_type, context=context
            )
            seen_values: set[int] = set()
            for case_value, _target in cases:
                if case_value in seen_values:
                    _fail(
                        "terminator",
                        func,
                        f"{context} repeats switch case {case_value}",
                    )
                seen_values.add(case_value)
        elif term.kind in {"br", "unreachable"}:
            continue
        else:
            _fail("terminator", func, f"{context} has unknown kind {term.kind!r}")


def verify_parsed_function(func: ParsedFunction) -> None:
    if not func.blocks:
        _fail("terminator", func, "function has no basic blocks")
    block_buckets, predecessors, successors = _build_cfg(func)
    dominators = _compute_dominators(predecessors, successors)
    definitions = _build_definitions(func)
    _verify_phis(
        func,
        definitions,
        predecessors,
        dominators,
        block_buckets,
    )
    _verify_ordinary_uses(func, definitions, dominators)
    _verify_terminator_types(func, definitions)


def verify_parsed_module(module: ParsedModule) -> None:
    for func in module.functions:
        verify_parsed_function(func)


__all__ = ["verify_parsed_function", "verify_parsed_module"]
