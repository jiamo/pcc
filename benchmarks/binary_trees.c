// Binary-trees benchmark
// From the Computer Language Benchmarks Game
// Stresses: memory allocation, recursion, pointer chasing
// Allocate and deallocate many binary trees

#include <stdlib.h>

typedef struct Node {
    struct Node *left, *right;
} Node;

static Node nodes[1 << 21]; // pool allocator
static int node_idx = 0;

static Node *new_node(void) {
    return &nodes[node_idx++];
}

static void reset_pool(void) {
    node_idx = 0;
}

static Node *make_tree(int depth) {
    Node *n = new_node();
    if (depth > 0) {
        n->left = make_tree(depth - 1);
        n->right = make_tree(depth - 1);
    } else {
        n->left = n->right = 0;
    }
    return n;
}

static int check_tree(const Node *n) {
    if (n->left)
        return 1 + check_tree(n->left) + check_tree(n->right);
    return 1;
}

int main(void) {
    int min_depth = 4;
    int max_depth = 18;
    int stretch_depth = max_depth + 1;
    long total_check = 0;

    // Stretch tree
    reset_pool();
    Node *stretch = make_tree(stretch_depth);
    total_check += check_tree(stretch);

    // Long-lived tree
    reset_pool();
    Node *long_lived = make_tree(max_depth);

    int depth;
    for (depth = min_depth; depth <= max_depth; depth += 2) {
        int iterations = 1 << (max_depth - depth + min_depth);
        int i;
        long check = 0;
        for (i = 0; i < iterations; i++) {
            reset_pool();
            Node *t = make_tree(depth);
            check += check_tree(t);
        }
        total_check += check;
    }

    total_check += check_tree(long_lived);
    return (int)(total_check % 256);
}
