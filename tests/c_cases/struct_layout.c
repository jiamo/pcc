// EXPECT: 0
/* Test struct layout: field offsets and sizeof with mixed types. */

struct Mixed {
    char a;
    int b;
    char c;
};

struct Node {
    char tag;
    long *data;
    char flag;
};

union Value {
    double n;
    long i;
    void *p;
};

struct TValue {
    union Value val;
    char tt;
};

struct WithArray {
    int x;
    int arr[3];
    int y;
};

typedef struct Foo {
    int x;
    int y;
} Foo;

int main() {
    int errors = 0;

    /* sizeof checks */
    if (sizeof(struct Mixed) != 12) errors++;
    if (sizeof(struct Node) != 24) errors++;
    if (sizeof(struct TValue) != 16) errors++;
    if (sizeof(int) != 4) errors++;
    if (sizeof(long) != 8) errors++;

    /* offsetof via pointer arithmetic */
    struct Mixed m;
    if ((char*)&m.b - (char*)&m != 4) errors++;

    /* struct field read/write */
    struct Mixed m2;
    m2.a = 10;
    m2.b = 20;
    m2.c = 30;
    if (m2.a + m2.b + m2.c != 60) errors++;

    /* typedef struct pointer access */
    Foo f;
    Foo *p = &f;
    p->x = 100;
    p->y = 200;
    if (p->x + p->y != 300) errors++;

    /* union via struct */
    struct TValue tv;
    tv.val.i = 42;
    tv.tt = 1;
    if (tv.val.i != 42) errors++;

    /* array in struct */
    struct WithArray wa;
    wa.x = 1;
    wa.arr[0] = 10;
    wa.arr[1] = 20;
    wa.arr[2] = 30;
    wa.y = 2;
    if (wa.x + wa.arr[0] + wa.arr[1] + wa.arr[2] + wa.y != 63) errors++;

    return errors;
}
