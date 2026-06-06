#ifndef _PCC_FAKE_STDATOMIC_H
#define _PCC_FAKE_STDATOMIC_H

/*
 * Parser-facing C11 atomics for pcc's -nostdinc preprocessing path.
 *
 * Keep the storage typedefs scalar: atomicity comes from the operations below,
 * which map to GCC __atomic_* builtins that pcc lowers to LLVM atomics.  This
 * avoids leaking target-specific Clang stdatomic internals (__c11_atomic_* and
 * wrapper structs) into the portable C frontend.
 */

#include <stdint.h>
#include <stddef.h>

typedef int memory_order;

#define memory_order_relaxed __ATOMIC_RELAXED
#define memory_order_consume __ATOMIC_CONSUME
#define memory_order_acquire __ATOMIC_ACQUIRE
#define memory_order_release __ATOMIC_RELEASE
#define memory_order_acq_rel __ATOMIC_ACQ_REL
#define memory_order_seq_cst __ATOMIC_SEQ_CST

typedef _Bool atomic_bool;
typedef char atomic_char;
typedef signed char atomic_schar;
typedef unsigned char atomic_uchar;
typedef short atomic_short;
typedef unsigned short atomic_ushort;
typedef int atomic_int;
typedef unsigned int atomic_uint;
typedef long atomic_long;
typedef unsigned long atomic_ulong;
typedef long long atomic_llong;
typedef unsigned long long atomic_ullong;

typedef int8_t atomic_int8_t;
typedef uint8_t atomic_uint8_t;
typedef int16_t atomic_int16_t;
typedef uint16_t atomic_uint16_t;
typedef int32_t atomic_int32_t;
typedef uint32_t atomic_uint32_t;
typedef int64_t atomic_int64_t;
typedef uint64_t atomic_uint64_t;
typedef intptr_t atomic_intptr_t;
typedef uintptr_t atomic_uintptr_t;
typedef size_t atomic_size_t;
typedef ptrdiff_t atomic_ptrdiff_t;

typedef unsigned char atomic_flag;

#define ATOMIC_VAR_INIT(value) (value)
#define ATOMIC_FLAG_INIT 0

#define atomic_init(object, desired) \
    __atomic_store_n((object), (desired), __ATOMIC_RELAXED)

#define atomic_load_explicit(object, order) \
    __atomic_load_n((object), (order))
#define atomic_load(object) \
    atomic_load_explicit((object), memory_order_seq_cst)

#define atomic_store_explicit(object, desired, order) \
    __atomic_store_n((object), (desired), (order))
#define atomic_store(object, desired) \
    atomic_store_explicit((object), (desired), memory_order_seq_cst)

#define atomic_fetch_add_explicit(object, operand, order) \
    __atomic_fetch_add((object), (operand), (order))
#define atomic_fetch_add(object, operand) \
    atomic_fetch_add_explicit((object), (operand), memory_order_seq_cst)
#define atomic_fetch_sub_explicit(object, operand, order) \
    __atomic_fetch_sub((object), (operand), (order))
#define atomic_fetch_sub(object, operand) \
    atomic_fetch_sub_explicit((object), (operand), memory_order_seq_cst)
#define atomic_fetch_or_explicit(object, operand, order) \
    __atomic_fetch_or((object), (operand), (order))
#define atomic_fetch_or(object, operand) \
    atomic_fetch_or_explicit((object), (operand), memory_order_seq_cst)
#define atomic_fetch_xor_explicit(object, operand, order) \
    __atomic_fetch_xor((object), (operand), (order))
#define atomic_fetch_xor(object, operand) \
    atomic_fetch_xor_explicit((object), (operand), memory_order_seq_cst)
#define atomic_fetch_and_explicit(object, operand, order) \
    __atomic_fetch_and((object), (operand), (order))
#define atomic_fetch_and(object, operand) \
    atomic_fetch_and_explicit((object), (operand), memory_order_seq_cst)

#define atomic_compare_exchange_strong_explicit( \
        object, expected, desired, success, failure) \
    __atomic_compare_exchange_n( \
        (object), (expected), (desired), 0, (success), (failure))
#define atomic_compare_exchange_strong(object, expected, desired) \
    atomic_compare_exchange_strong_explicit( \
        (object), (expected), (desired), \
        memory_order_seq_cst, memory_order_seq_cst)
#define atomic_compare_exchange_weak_explicit( \
        object, expected, desired, success, failure) \
    __atomic_compare_exchange_n( \
        (object), (expected), (desired), 1, (success), (failure))
#define atomic_compare_exchange_weak(object, expected, desired) \
    atomic_compare_exchange_weak_explicit( \
        (object), (expected), (desired), \
        memory_order_seq_cst, memory_order_seq_cst)

#define atomic_flag_test_and_set_explicit(object, order) \
    __atomic_test_and_set((object), (order))
#define atomic_flag_test_and_set(object) \
    atomic_flag_test_and_set_explicit((object), memory_order_seq_cst)
#define atomic_flag_clear_explicit(object, order) \
    __atomic_clear((object), (order))
#define atomic_flag_clear(object) \
    atomic_flag_clear_explicit((object), memory_order_seq_cst)

/* Pcc lowers __sync_synchronize to a sequentially consistent LLVM fence. */
#define atomic_thread_fence(order) ((void)(order), __sync_synchronize())
#define atomic_signal_fence(order) ((void)(order), __sync_synchronize())

#define kill_dependency(value) (value)

#endif
