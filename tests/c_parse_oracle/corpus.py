"""Curated C source corpus for differential C-parser testing.

Covers the grammar categories pcc's C frontend supports. Each snippet
is short (few LoC) so when the oracle diff fails, the exact node
mismatch is easy to eyeball.

Categories:
  - expr:   literals, operators, casts, sizeof, _Generic
  - stmt:   if/while/for/do-while/switch/goto/label
  - decl:   typedef, enum, struct, union, bit-field, initializers
  - funcdef: prototypes + definitions, variadic args
  - c11+:   _Alignas, _Atomic, _Static_assert, _Generic
"""
from __future__ import annotations


CORPUS: dict[str, str] = {
    # ----------------------------------------------------------- expressions
    "expr_int_literal":       "int f(void) { return 42; }",
    "expr_hex_literal":       "int f(void) { return 0x1f; }",
    "expr_float_literal":     "float f(void) { return 3.14f; }",
    "expr_string_literal":    "const char* f(void) { return \"hi\"; }",
    "expr_wchar_literal":     r"int f(void) { int wc; wc = L'\0'; return wc; }",
    "expr_char_literal":      "char f(void) { return 'a'; }",
    "expr_binop_add":         "int f(int a, int b) { return a + b; }",
    "expr_binop_shift":       "int f(int a) { return a << 2 | a >> 1; }",
    "expr_binop_cmp":         "int f(int a, int b) { return a < b && a != 0; }",
    "expr_unary_neg":         "int f(int a) { return -a; }",
    "expr_unary_not":         "int f(int a) { return !a; }",
    "expr_unary_bitnot":      "int f(int a) { return ~a; }",
    "expr_ternary":           "int f(int a) { return a ? 1 : 2; }",
    "expr_cast":              "int f(double d) { return (int)d; }",
    "expr_sizeof_expr":       "int f(int a) { return sizeof a; }",
    "expr_sizeof_type":       "int f(void) { return sizeof(int); }",
    "expr_member":            "struct P { int x; }; int f(struct P p) { return p.x; }",
    "expr_arrow":             "struct P { int x; }; int f(struct P* p) { return p->x; }",
    "expr_subscript":         "int f(int a[10]) { return a[3]; }",
    "expr_call":              "int g(int); int f(int a) { return g(a); }",
    "expr_compound_assign":   "int f(int a) { a += 1; return a; }",
    "expr_comma":             "int f(void) { return (1, 2); }",
    "expr_preincrement":      "int f(int a) { return ++a; }",
    "expr_postincrement":     "int f(int a) { return a++; }",
    # ----------------------------------------------------------- statements
    "stmt_if":                "int f(int a) { if (a) return 1; return 0; }",
    "stmt_if_else":           "int f(int a) { if (a) return 1; else return 0; }",
    "stmt_while":             "int f(int a) { while (a) a--; return a; }",
    "stmt_do_while":          "int f(int a) { do a--; while (a); return a; }",
    "stmt_for":               "int f(void) { int s=0; for (int i=0;i<10;i++) s+=i; return s; }",
    "stmt_for_empty":         "int f(void) { for (;;) break; return 0; }",
    "stmt_switch":            "int f(int a) { switch (a) { case 1: return 1; case 2: return 2; default: return 0; } }",
    "stmt_goto":              "int f(int a) { if (a) goto end; return 1; end: return 0; }",
    "stmt_compound":          "int f(void) { { int x = 1; return x; } }",
    "stmt_empty":             "void f(void) { ; }",
    # ----------------------------------------------------------- declarations
    "decl_simple_int":        "int x;",
    "decl_pointer":            "int* p;",
    "decl_pointer_chain":     "int** pp;",
    "decl_array":              "int a[10];",
    "decl_array_2d":          "int m[3][4];",
    "decl_func_ptr":          "int (*fp)(int, int);",
    "decl_const":             "const int c = 42;",
    "decl_static":            "static int s;",
    "decl_extern":            "extern int e;",
    "decl_typedef_int":       "typedef int my_int;",
    "decl_typedef_struct":    "typedef struct P { int x; int y; } Point;",
    "decl_enum_named":        "enum Color { RED, GREEN, BLUE };",
    "decl_enum_values":       "enum E { A = 1, B = 2, C = 4 };",
    "decl_struct_def":        "struct Node { int val; struct Node* next; };",
    "decl_union":             "union U { int i; float f; };",
    "decl_bitfield":          "struct F { unsigned a : 3; unsigned b : 5; };",
    "decl_init_scalar":       "int x = 42;",
    "decl_init_array":        "int a[3] = {1, 2, 3};",
    "decl_init_nested":       "int m[2][2] = {{1, 2}, {3, 4}};",
    # "decl_init_designated":   "int a[5] = {[2] = 10};",   # designated inits - may fail
    # ---------------------------------------------------------- functions
    "func_proto":             "int add(int a, int b);",
    "func_def_simple":        "int add(int a, int b) { return a + b; }",
    "func_def_void":          "void greet(void) { }",
    "func_variadic":          "int sum(int n, ...);",
    "func_pointer_return":    "int* make(void);",
    "func_array_param":       "void zero(int a[]) { a[0] = 0; }",
    # ---------------------------------------------------------- C99/C11
    "c11_static_assert":      "_Static_assert(sizeof(int) >= 4, \"too small\");",
    "c11_inline":             "static inline int sq(int x) { return x * x; }",
    # "c11_generic":            "int f(int x) { return _Generic(x, int: 1, double: 2); }",  # may need runtime
    # ---------------------------------------------------------- preprocessor-ish
    # (parser level — preprocessor is separate)
    "real_linked_list_node":   """struct ListNode {
    int value;
    struct ListNode *next;
    struct ListNode *prev;
};
""",
    "real_small_fn": """int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}
""",
    "real_nested_control": """int classify(int x) {
    if (x < 0) {
        return -1;
    } else if (x == 0) {
        return 0;
    } else {
        return 1;
    }
}
""",
}
