from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import TranslationUnit


def _evaluate(source, optimize=True):
    return CEvaluator().evaluate(source, optimize=optimize)


def _run_with_system_link(source):
    unit = TranslationUnit("compat_main.c", "compat_main.c", source)
    return CEvaluator().run_translation_units_with_system_cc(
        [unit],
        optimize=True,
        base_dir=".",
        jobs=1,
    )


def test_struct_rvalue_member_access_from_function_return():
    source = r"""
        struct Pair {
            int left;
            int right;
        };

        struct Pair make_pair(void) {
            struct Pair value;
            value.left = 7;
            value.right = 11;
            return value;
        }

        int main(void) {
            return make_pair().right == 11 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_clang_builtins_and___func___work_with_system_link():
    source = r"""
        int helper(int x) {
            unsigned int rotated = __builtin_rotateleft32(0x12345678u, 8);
            unsigned long long swapped =
                __builtin_bswap64(0x0102030405060708ULL);
            int zeros = __builtin_ctzll(0x100ULL) + __builtin_clz(1u);

            if (__func__[0] != 'h') return 1;
            if (rotated != 0x34567812u) return 2;
            if (swapped != 0x0807060504030201ULL) return 3;
            if (zeros != 39) return 4;
            if (__builtin_expect(x == 7, 1)) return 0;

            __builtin_unreachable();
        }

        int main(void) {
            return helper(7);
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"clang builtin compat failed:\n{result.stdout}\n{result.stderr}"


def test_c11_noreturn_function_specifier_parses_in_prototype_and_definition():
    source = r"""
        _Noreturn void die(void);

        int helper(void) {
            return 7;
        }

        _Noreturn void die(void) {
            for (;;) {
            }
        }

        int main(void) {
            return helper() == 7 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_block_scope_function_prototype_reuses_named_callee():
    source = r"""
        int f1(char *p) {
            return *p + 1;
        }

        int main(void) {
            char s = 1;
            int f1(char *);

            return f1(&s) == 2 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_builtin_constant_p_falls_back_to_runtime_path_with_system_cpp():
    source = r"""
        int classify(unsigned short value) {
            return __builtin_constant_p(value) ? 1 : 2;
        }

        int main(void) {
            return classify(7) == 2 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"__builtin_constant_p compat failed:\n{result.stdout}\n{result.stderr}"


def test_system_cpp_normalizes_choose_expr_malloc_abs_and_sync_add_fetch():
    source = r"""
        extern void *malloc(unsigned long);
        extern void free(void *);
        extern int abs(int);

        int main(void) {
            int x = 0;
            int *p = __builtin_malloc(sizeof(int));

            *p = __builtin_abs(-7);
            if (__builtin_choose_expr(1, *p, 0) != 7)
                return 1;
            if (__sync_add_and_fetch(&x, 3) != 3)
                return 2;

            __builtin_free(p);
            return 0;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"builtin macro compat failed:\n{result.stdout}\n{result.stderr}"


def test_builtin_overflow_helpers_track_result_and_flag():
    source = r"""
        int main(void) {
            unsigned long long add = 123;
            unsigned long long sub = 123;
            unsigned long long mul = 123;

            if (__builtin_add_overflow(0xffffffffffffffffULL, 1ULL, &add) != 1)
                return 1;
            if (add != 0ULL)
                return 2;

            if (__builtin_sub_overflow(0ULL, 1ULL, &sub) != 1)
                return 3;
            if (sub != 0xffffffffffffffffULL)
                return 4;

            if (__builtin_mul_overflow(3ULL, 7ULL, &mul) != 0)
                return 5;
            if (mul != 21ULL)
                return 6;

            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_builtin_alloca_allocates_runtime_stack_storage():
    source = r"""
        int main(void) {
            char *buf = (char *)__builtin_alloca(4);
            buf[0] = 'o';
            buf[1] = 'k';
            buf[2] = 0;
            return (buf[0] == 'o' && buf[1] == 'k' && buf[2] == 0) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_alignof_extension_uses_alignment_not_size():
    source = r"""
        int main(void) {
            if (__alignof__(void) != 1)
                return 1;
            if (__alignof__(double) != 8)
                return 2;
            if (__alignof__(long double) != 8)
                return 3;
            if (__alignof__(void (*)()) != 8)
                return 4;
            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_mixed_wide_string_and_wide_char_constant_compare_correctly():
    source = r"""
        int main(void) {
            return L"a" "b"[1] == L'b' ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"wide string/char compat failed:\n{result.stdout}\n{result.stderr}"


def test_vla_array_indexing_works_for_non_integer_element_types():
    source = r"""
        int f(int n) {
            int i;
            double v[n];
            for (i = 0; i < n; i++)
                v[i] = 0.0;
            return 0;
        }

        int main(void) {
            return f(8);
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"VLA indexing compat failed:\n{result.stdout}\n{result.stderr}"


def test_floating_increment_and_decrement_lower_with_fadd_fsub():
    source = r"""
        int main(void) {
            double x = 1.0;
            double y = 2.0;

            if ((y > x--) != 1)
                return 1;
            if (x != 0.0)
                return 2;
            if (++y != 3.0)
                return 3;
            return 0;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"floating inc/dec compat failed:\n{result.stdout}\n{result.stderr}"


def test_builtin_va_arg_gnu_syntax_parses_and_runs():
    source = r"""
        typedef char *__builtin_va_list;
        typedef __builtin_va_list va_list;

        int pull_int(const char *fmt, ...) {
            va_list ap;
            int value;
            __builtin_va_start(ap, fmt);
            value = __builtin_va_arg(ap, int);
            __builtin_va_end(ap);
            return value;
        }

        int main(void) {
            return pull_int("", 17) == 17 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"builtin va_arg GNU syntax failed:\n{result.stdout}\n{result.stderr}"


def test_builtin_va_arg_lowered_shape_parses_and_runs():
    source = r"""
        typedef char *__builtin_va_list;
        typedef __builtin_va_list va_list;

        int pull_int(const char *fmt, ...) {
            va_list ap;
            int value;
            __builtin_va_start(ap, fmt);
            value = (*((int*)__builtin_va_arg(&(ap), sizeof(int))));
            __builtin_va_end(ap);
            return value;
        }

        int main(void) {
            return pull_int("", 17) == 17 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"builtin va_arg lowered shape failed:\n{result.stdout}\n{result.stderr}"


def test_empty_named_union_definition_is_zero_sized_but_loadable():
    source = r"""
        union U {} g = {};

        union U retg(void) {
            return g;
        }

        int main(void) {
            union U x = retg();
            (void)x;
            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_float_suffix_global_constant_init_matches_runtime_expression():
    source = r"""
        extern void abort(void);

        double d = 1.17549435e-38F / 2.0;

        int main(void) {
            double x = 1.17549435e-38F / 2.0;
            if (x != d)
                abort();
            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_union_array_named_initializers_select_the_target_member():
    source = r"""
        typedef long W;

        union U {
            union U *r;
            W i;
        };

        int main(void) {
            union U uv[4] = {{.i = 111}, {.i = 222}, {.i = 333}, {.i = 444}};
            return uv[0].i == 111
                && uv[1].i == 222
                && uv[2].i == 333
                && uv[3].i == 444
                ? 0
                : 1;
        }
    """

    assert _evaluate(source) == 0


def test_pointer_to_array_local_declaration_indexes_nested_elements():
    source = r"""
        int main(void) {
            char arr[2][4];
            char (*p)[4];
            char *q;
            int v[4];

            p = arr;
            q = &arr[1][3];
            arr[1][3] = 2;
            v[0] = 2;

            if (arr[1][3] != 2) return 1;
            if (p[1][3] != 2) return 2;
            if (*q != 2) return 3;
            if (*v != 2) return 4;
            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_gnu_binary_conditional_reuses_condition_value():
    source = r"""
        int main(void) {
            int a = 0;
            int b = 7;

            if ((++a ?: b) != 1)
                return 1;
            if (a != 1)
                return 2;
            if ((0 ?: b) != 7)
                return 3;
            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_system_cpp_normalizes_typeof_sizeof_size_t_typedef():
    source = r"""
        typedef __typeof(sizeof(int)) size_t;

        int main(void) {
            size_t value = sizeof(int);
            return value == sizeof(int) ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"__typeof(sizeof(int)) size_t compat failed:\n{result.stdout}\n{result.stderr}"


def test_void_function_can_return_void_valued_expression():
    source = r"""
        static void sink(void) {
        }

        static void wrapper(void) {
            return sink();
        }

        int main(void) {
            wrapper();
            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_system_cpp_supports_function_name_and_basic_string_builtins():
    source = r"""
        #include <string.h>

        static int helper(void) {
            if (__builtin_strlen("ok") != 2)
                return 1;
            if (__builtin_strcmp(__FUNCTION__, "helper") != 0)
                return 2;
            return 0;
        }

        int main(void) {
            if (helper() != 0)
                __builtin_abort();
            return 0;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"__FUNCTION__/builtin string compat failed:\n{result.stdout}\n{result.stderr}"


def test_builtin_ffs_reports_one_based_lowest_set_bit():
    source = r"""
        int main(void) {
            volatile int zero = 0;
            if (__builtin_ffs(zero) != 0)
                return 1;
            if (__builtin_ffs(0x8000) != 16)
                return 2;
            if (__builtin_ffs(0xa5a5) != 1)
                return 3;
            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_builtin_frame_address_zero_returns_current_frame_storage():
    source = r"""
        static int check_frame_inner(const char *c, const char *f) {
            const char d = 0;

            if (c >= &d)
                return c >= f && f >= &d;
            return c <= f && f <= &d;
        }

        static int check_frame_mid(const char *c) {
            const char *f = __builtin_frame_address(0);
            return check_frame_inner(c, f) != 0;
        }

        static int check_frame(char *unused) {
            const char c = 0;
            (void)unused;
            return check_frame_mid(&c) != 0;
        }

        int main(void) {
            char *unused = __builtin_alloca(8);
            return check_frame(unused) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_builtin_float_classification_helpers_work_with_system_link():
    source = r"""
        int main(void) {
            double nan = __builtin_nan("");
            double inf = __builtin_inf();

            if (!__builtin_isnan(nan))
                return 1;
            if (__builtin_isnan(1.0))
                return 2;
            if (!__builtin_isinf(-inf))
                return 3;
            if (!__builtin_isfinite(1.0))
                return 4;
            if (__builtin_isfinite(inf))
                return 5;
            if (!__builtin_isless(1.0, 2.0))
                return 6;
            if (!__builtin_islessequal(2.0, 2.0))
                return 7;
            if (!__builtin_isgreater(2.0, 1.0))
                return 8;
            if (!__builtin_isgreaterequal(2.0, 2.0))
                return 9;
            if (!__builtin_signbit(-0.0))
                return 10;
            if (__builtin_signbit(0.0))
                return 11;
            return 0;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"float builtin compat failed:\n{result.stdout}\n{result.stderr}"


def test_system_cpp_normalizes_asm_volatile_and_simple_range_designators():
    source = r"""
        typedef unsigned char u8;

        static u8 buf[8] = { [0 ... 7] = 0xaa };

        static int barrier(int x) {
            asm volatile ("" : : "r" (x) : "memory");
            return x;
        }

        int main(void) {
            return barrier(buf[3]) == 0xaa ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"asm/range-designator compat failed:\n{result.stdout}\n{result.stderr}"


def test_file_scope_struct_with_floats_and_bitfields_keeps_custom_layout():
    source = r"""
        struct Row {
            double x;
            double y;
            unsigned a : 1;
            unsigned b : 1;
            unsigned c : 2;
            unsigned d : 1;
            unsigned e : 1;
        };

        static const struct Row rows[] = {
            { 0.0 / 0.0, 1.0, 1, 0, 0, 0, 0 },
            { 1.0, 2.0, 0, 1, 1, 0, 1 }
        };

        int main(void) {
            if (rows[0].x == rows[0].x)
                return 1;
            if (rows[0].y != 1.0)
                return 2;
            if (!rows[0].a || rows[0].b)
                return 3;
            if (rows[1].y != 2.0)
                return 4;
            if (rows[1].c != 1 || !rows[1].e)
                return 5;
            return 0;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"custom-layout const init failed:\n{result.stdout}\n{result.stderr}"


def test_runtime_designated_initializer_targets_named_struct_fields():
    source = r"""
        struct S {
            unsigned char c1;
            unsigned char c2 : 1;
            unsigned char c3 : 3;
            int value;
        };

        static struct S make(void) {
            struct S s = { .value = 7, .c3 = 5, .c1 = 2, .c2 = 1 };
            return s;
        }

        int main(void) {
            struct S s = make();
            return (s.c1 == 2 && s.c2 == 1 && s.c3 == 5 && s.value == 7) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_designated_array_initializer_infers_missing_elements_and_count():
    source = r"""
        int values[] = { 5, [2] = 2, 3 };

        int main(void) {
            return (
                sizeof(values) == 4 * sizeof(int) &&
                values[0] == 5 &&
                values[1] == 0 &&
                values[2] == 2 &&
                values[3] == 3
            ) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_designated_array_initializer_reorders_struct_elements():
    source = r"""
        struct Pair {
            int a;
            int b;
        };

        struct Pair pairs[2] = { [1] = {3, 4}, [0] = {1, 2} };

        int main(void) {
            return (
                pairs[0].a == 1 &&
                pairs[0].b == 2 &&
                pairs[1].a == 3 &&
                pairs[1].b == 4
            ) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_compound_literal_materializes_full_struct_object():
    source = r"""
        struct U {
            char c[16];
        };

        int main(void) {
            struct U u = (struct U) { "abcdefghijklmno" };
            return (u.c[0] == 'a' && u.c[14] == 'o' && u.c[15] == 0) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_old_style_function_declaration_uses_later_definition_signature():
    source = r"""
        static int value;
        static int *h();

        static int *call(void) {
            return h(1u, 2);
        }

        static int *h(unsigned a, int b) {
            value = (int)(a + (unsigned)b);
            return &value;
        }

        int main(void) {
            return *call() == 3 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_function_typed_parameter_decl_decays_to_function_pointer():
    source = r"""
        typedef int (*fptr1)();
        int f1 (int (), int);

        static int add1(int i) {
            return i + 1;
        }

        int f1 (fptr1 fp, int i) {
            return (*fp)(i);
        }

        int main(void) {
            return f1(add1, 2) == 3 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_string_literal_pointer_arithmetic_decays_array_before_addition():
    source = r"""
        extern void *memchr(const void *, int, unsigned long);

        int main(void) {
            return memchr("" + 1, 0, 0) == 0 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"string literal array decay failed:\n{result.stdout}\n{result.stderr}"


def test_array_value_decays_to_pointer_when_passed_through_default_promotions():
    source = r"""
        extern unsigned long strlen(const char *);

        int main(void) {
            static const char a[2][4] = { "abc", "xy" };
            return strlen(*(&a[0] + 1)) == 2 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"array-value decay failed:\n{result.stdout}\n{result.stderr}"


def test_pointer_to_array_expression_decays_before_pointer_arithmetic():
    source = r"""
        extern unsigned long strlen(const char *);

        typedef char row_t[8];
        typedef row_t rows_t[3];

        static const rows_t a = { "1", "123", "12345" };
        static const rows_t *const pa0 = &a;

        int main(void) {
            int i1 = 1;
            return strlen(*(pa0[0] + i1)) == 3 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"pointer-to-array decay failed:\n{result.stdout}\n{result.stderr}"


def test_dereferenced_pointer_to_array_keeps_original_storage_for_subtraction():
    source = r"""
        extern unsigned long strlen(const char *);

        typedef char row_t[16];
        typedef row_t rows_t[3];
        typedef rows_t table_t[2];

        static const table_t a = {
            { "1", "123", "12345" },
            { "1234567", "123456789", "12345678901" }
        };
        static const rows_t *const paa[] = { &a[0], &a[1] };

        int main(void) {
            int i1 = 1;
            return strlen(*(*(paa[1]) - i1)) == 5 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"dereferenced pointer-to-array storage failed:\n{result.stdout}\n{result.stderr}"


def test_gnu_old_style_designator_and_empty_compound_literal_work():
    source = r"""
        struct S {
            int a:3;
            unsigned b:1, c:28;
        };

        int main(void) {
            struct S x = { b:0, a:0, c:7 };
            struct S y = (struct S){};
            return x.a == 0 && x.b == 0 && x.c == 7 && y.c == 0 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_int128_keyword_parses_and_preserves_wide_switch_values():
    source = r"""
        unsigned char classify(__int128 value) {
            switch (value) {
            case 0:
                return 1;
            case 1:
                return 2;
            default:
                return 3;
            }
        }

        int main(void) {
            volatile __int128 one = 1;
            __int128 wide = ((__int128)1) << 64;
            return classify(one) == 2 && classify(wide) == 3 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_system_cpp_normalizes_simple_typeof_identifier_declarations():
    source = r"""
        struct s { int a; } x;
        typeof(x) y;

        struct foo { int value; };
        typedef struct foo bar;
        bar x1;

        int main(void) {
            typeof(x1) y1;
            y.a = 7;
            y1.value = 9;
            return sizeof(y) == sizeof(x) && sizeof(y1) == sizeof(x1) ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"typeof(identifier) compat failed:\n{result.stdout}\n{result.stderr}"


def test_knr_variadic_definition_inherits_prior_prototype():
    source = r"""
        #include <stdarg.h>

        int test(char *fmt, ...);

        int test(fmt)
            char *fmt;
        {
            va_list ap;
            char *value;

            va_start(ap, fmt);
            value = va_arg(ap, char *);
            va_end(ap);
            return value[0] == 'o' && value[1] == 'k' ? 0 : 1;
        }

        int main(void) {
            return test("", "ok");
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"K&R variadic definition compat failed:\n{result.stdout}\n{result.stderr}"


def test_implicit_function_declaration_uses_future_typed_definition():
    source = r"""
        typedef struct {
            char y;
            char x[32];
        } X;

        int z(void) {
            X value;
            value.x[0] = value.x[31] = '0';
            value.y = 0xf;
            return f(value, value);
        }

        int main(void) {
            return z() == 0x60 ? 0 : 1;
        }

        int f(X x, X y) {
            if (x.y != y.y)
                return 'F';
            return x.x[0] + y.x[0];
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"implicit function future-typed definition compat failed:\n{result.stdout}\n{result.stderr}"


def test_implicit_zero_arg_function_definition_compiles_under_gnu89_rules():
    source = r"""
        int main(void) {
            f();
            return 0;
        }

        f() {}
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"implicit zero-arg function compat failed:\n{result.stdout}\n{result.stderr}"


def test_gnu_builtins_map_to_runtime_equivalents():
    source = r"""
        char buf[64];

        int main(void) {
            __builtin_strcpy(buf, "mystring");
            if (__builtin_strcmp(buf, "mystring") != 0)
                return 1;

            __builtin_sprintf(buf, "%d", 42);
            if (__builtin_strcmp(buf, "42") != 0)
                return 2;

            if (0)
                __builtin_trap();

            return 0;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"GNU builtin alias compat failed:\n{result.stdout}\n{result.stderr}"


def test_alloca_without_builtin_prefix_and_classify_type_work():
    source = r"""
        int main(void) {
            char *buf = (char *)alloca(4);
            long double value = 1.25L;

            buf[0] = 7;
            if (buf[0] != 7)
                return 1;
            if (__builtin_classify_type(value) != 8)
                return 2;
            return 0;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"alloca/classify_type compat failed:\n{result.stdout}\n{result.stderr}"


def test_system_cpp_strips_single_underscore_attribute_form():
    source = r"""
        int main(void) {
            __attribute((aligned(32))) union {
                int i;
                long long ll;
            } value;

            value.i = 7;
            return value.i == 7 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"__attribute compat failed:\n{result.stdout}\n{result.stderr}"


def test_function_type_typedef_can_declare_and_define_static_function():
    source = r"""
        typedef int (callback_t)(int value);

        static callback_t plus_one;

        static int plus_one(int value) {
            return value + 1;
        }

        int main(void) {
            return plus_one(6) == 7 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_system_cpp_normalizes_atomic_type_sugar():
    source = r"""
        typedef _Atomic(int) atomic_int_alias;

        int main(void) {
            atomic_int_alias value = 7;
            return value == 7 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"_Atomic compat failed:\n{result.stdout}\n{result.stderr}"


def test_float16_keyword_parses_as_floating_scalar():
    source = r"""
        int main(void) {
            _Float16 half = 1.5f;
            float value = half;
            return value > 1.0f && value < 2.0f ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_int_literal_width_is_preserved_through_bitwise_ops():
    source = r"""
        int main(void) {
            unsigned int high_bit = ~((~0U) >> 1);
            return high_bit == 0x80000000U ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_int_to_float_conversion_uses_unsigned_semantics():
    source = r"""
        int main(void) {
            unsigned int high_bit = ~((~0U) >> 1);
            float value = (float)high_bit;
            return value > 0.0f && value == 2147483648.0f ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_file_scope_floating_initializer_preserves_constant_expression_value():
    source = r"""
        double d = 1024.0 - 1.0 / 32768.0;

        int main(void) {
            return d < 1024.0 && d > 1023.0 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_ieee_float_builtins_handle_nan_inf_and_copysign():
    source = r"""
        int main(void) {
            double inf = __builtin_inf();
            double nan = __builtin_nan("");
            double flipped = __builtin_copysign(1.0, -0.0);

            if (!__builtin_isunordered(nan, nan))
                return 1;
            if (__builtin_islessgreater(nan, inf))
                return 2;
            if (__builtin_islessgreater(1.0, 1.0))
                return 3;
            if (!__builtin_islessgreater(1.0, 2.0))
                return 4;
            return flipped == -1.0 ? 0 : 5;
        }
    """

    assert _evaluate(source) == 0


def test_file_scope_builtin_inf_and_nan_initializers_keep_special_values():
    source = r"""
        double inf = __builtin_inf();
        double nan = __builtin_nan("");

        int main(void) {
            return inf > 1.0 && nan != nan ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_sizeof_dereferenced_array_uses_element_type():
    source = r"""
        static const unsigned short ctype_char_map[4] = { 1, 2, 3, 4 };

        int main(void) {
            return sizeof(ctype_char_map) / sizeof(*ctype_char_map) == 4 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_enum_typed_local_declaration_uses_enum_ir_type():
    source = r"""
        enum header_status {
            MAYBE_HEADER,
            IN_HEADER
        };

        int main(void) {
            enum header_status got_header = MAYBE_HEADER;
            return got_header == 0 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_named_struct_definition_with_anonymous_enum_field_completes_tag():
    source = r"""
        typedef struct Box Box;

        struct Box {
            int value;
            enum { BOX_ZERO, BOX_ONE } kind;
        };

        int main(void) {
            return BOX_ONE == 1 && sizeof(((Box *)0)->kind) == sizeof(int) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_pointer_to_enum_type_declares_and_participates_in_conditional_expression():
    source = r"""
        enum e { ENUM_MIN = -2147483647 - 1 };

        int *p;
        enum e *q;

        int main(void) {
            enum e x = ENUM_MIN;
            q = &x;
            return (*(1 ? q : p) < 0) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_incomplete_extern_array_can_be_used_before_later_definition():
    source = r"""
        extern unsigned long aa[], bb[];

        static int seqgt(unsigned long a, unsigned short win, unsigned long b) {
            return (long)((a + win) - b) > 0;
        }

        static int seqgt2(unsigned long a, unsigned short win, unsigned long b) {
            long l = ((a + win) - b);
            return l > 0;
        }

        int main(void) {
            return seqgt(*aa, 0x1000, *bb) && seqgt2(*aa, 0x1000, *bb) ? 0 : 1;
        }

        unsigned long aa[] = { (1UL << (sizeof(long) * 8 - 1)) - 0xfff };
        unsigned long bb[] = { (1UL << (sizeof(long) * 8 - 1)) - 0xfff };
    """

    assert _evaluate(source) == 0


def test_vla_parameter_bound_side_effects_run_on_function_entry():
    source = r"""
        static int sub(int i, int array[i++]) {
            return i;
        }

        int main(void) {
            int array[10];
            return sub(10, array) == 11 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_global_pointer_initializer_accepts_struct_member_address_expression():
    source = r"""
        typedef struct foo {
            int uaattrid;
            char *name;
        } FOO;

        FOO upgrade_items[] = {
            {1, "1"},
            {2, "2"},
            {0, 0}
        };

        int *minor_id = (int *)&((upgrade_items + 1)->uaattrid);
        int *minor_id1 = (int *)&((upgrade_items)->uaattrid);

        int main(void) {
            return *minor_id == 2 && *minor_id1 == 1 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unnamed_array_parameter_prototype_matches_named_definition():
    source = r"""
        int sum_values(int []);

        int sum_values(int values[]) {
            return values[0] + values[1];
        }

        int main(void) {
            int values[2] = {2, 5};
            return sum_values(values) == 7 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_sizeof_self_in_initializer_uses_declared_type():
    source = r"""
        int main(void) {
            unsigned long long word =
                sizeof(word) == sizeof(unsigned long long) ? 11ULL : 7ULL;
            return word == 11ULL ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_uintptr_and_intptr_match_pointer_width():
    source = r"""
        #include <stdint.h>

        int main(void) {
            char buf[8];
            uintptr_t start = (uintptr_t)buf;
            uintptr_t end = (uintptr_t)(buf + 3);
            intptr_t signed_delta = (intptr_t)(end - start);

            if (sizeof(uintptr_t) != sizeof(void *))
                return 1;
            if (sizeof(intptr_t) != sizeof(void *))
                return 2;
            if (end <= start)
                return 3;
            return signed_delta == 3 ? 0 : 4;
        }
    """

    assert _evaluate(source) == 0


def test_stdint_exact_width_typedefs_preserve_element_stride():
    source = r"""
        #include <stdint.h>

        int main(void) {
            uint8_t bytes[4] = {0xc0, 0x68, 0x65, 0x6c};
            uint8_t *p = bytes;

            if (sizeof(uint8_t) != 1) return 1;
            if (sizeof(uint16_t) != 2) return 2;
            if (sizeof(uint32_t) != 4) return 3;
            if (sizeof(uint64_t) != 8) return 4;
            if ((p + 1) != &bytes[1]) return 5;
            if ((unsigned)*p != 0xc0u) return 6;
            return (unsigned)p[1] == 0x68u ? 0 : 7;
        }
    """

    assert _evaluate(source) == 0


def test_knr_definition_matches_prior_prototype():
    source = r"""
        static void set_winsize(int);

        static void set_winsize(tty)
            int tty;
        {
            (void)tty;
        }

        int main(void) {
            set_winsize(0);
            return 0;
        }
    """

    assert _evaluate(source) == 0


def test_fortify_sprintf_chk_falls_back_to_sprintf_with_system_cpp():
    source = r"""
        #include <stdio.h>
        #include <string.h>

        int main(void) {
            char buf[32];
            __builtin___sprintf_chk(buf, 0, sizeof(buf), "%s %d", "ok", 7);
            return strcmp(buf, "ok 7") == 0 ? 0 : 1;
        }
    """

    result = _run_with_system_link(source)

    assert (
        result.returncode == 0
    ), f"__builtin___sprintf_chk compat failed:\n{result.stdout}\n{result.stderr}"


def test_errno_macros_include_erange():
    source = r"""
        #include <errno.h>

        int main(void) {
            return (EINVAL != 0 && ERANGE != 0) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_fcntl_macros_include_status_flags():
    source = r"""
        #include <fcntl.h>

        int main(void) {
            return (F_GETFL != 0 && F_SETFL != 0 && O_NONBLOCK != 0) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_nested_block_scope_restores_outer_binding_after_shadowing():
    source = r"""
        int main(void) {
            int value = 7;
            int *result = &value;

            {
                int result = 99;
                if (result != 99)
                    return 1;
            }

            return *result == 7 ? 0 : 2;
        }
    """

    assert _evaluate(source) == 0
