// Red-black tree insertion/search benchmark
// Stresses: pointer manipulation, struct operations, balanced tree, conditional branches

#define MAX_NODES 500000
#define RED 0
#define BLACK 1

struct RBNode {
    int key;
    int color;
    int parent;  // index, -1 for nil
    int left;
    int right;
};

static struct RBNode nodes[MAX_NODES + 1]; // index 0 = nil sentinel
static int node_count;
static int root;

static void init_tree(void) {
    node_count = 1; // 0 is nil
    nodes[0].color = BLACK;
    nodes[0].key = 0;
    nodes[0].parent = 0;
    nodes[0].left = 0;
    nodes[0].right = 0;
    root = 0;
}

static int new_node(int key) {
    int n = node_count++;
    nodes[n].key = key;
    nodes[n].color = RED;
    nodes[n].parent = 0;
    nodes[n].left = 0;
    nodes[n].right = 0;
    return n;
}

static void rotate_left(int x) {
    int y = nodes[x].right;
    nodes[x].right = nodes[y].left;
    if (nodes[y].left != 0) nodes[nodes[y].left].parent = x;
    nodes[y].parent = nodes[x].parent;
    if (nodes[x].parent == 0)
        root = y;
    else if (x == nodes[nodes[x].parent].left)
        nodes[nodes[x].parent].left = y;
    else
        nodes[nodes[x].parent].right = y;
    nodes[y].left = x;
    nodes[x].parent = y;
}

static void rotate_right(int x) {
    int y = nodes[x].left;
    nodes[x].left = nodes[y].right;
    if (nodes[y].right != 0) nodes[nodes[y].right].parent = x;
    nodes[y].parent = nodes[x].parent;
    if (nodes[x].parent == 0)
        root = y;
    else if (x == nodes[nodes[x].parent].right)
        nodes[nodes[x].parent].right = y;
    else
        nodes[nodes[x].parent].left = y;
    nodes[y].right = x;
    nodes[x].parent = y;
}

static void insert_fixup(int z) {
    while (nodes[nodes[z].parent].color == RED) {
        int zp = nodes[z].parent;
        int zpp = nodes[zp].parent;
        if (zp == nodes[zpp].left) {
            int y = nodes[zpp].right;
            if (nodes[y].color == RED) {
                nodes[zp].color = BLACK;
                nodes[y].color = BLACK;
                nodes[zpp].color = RED;
                z = zpp;
            } else {
                if (z == nodes[zp].right) {
                    z = zp;
                    rotate_left(z);
                    zp = nodes[z].parent;
                    zpp = nodes[zp].parent;
                }
                nodes[zp].color = BLACK;
                nodes[zpp].color = RED;
                rotate_right(zpp);
            }
        } else {
            int y = nodes[zpp].left;
            if (nodes[y].color == RED) {
                nodes[zp].color = BLACK;
                nodes[y].color = BLACK;
                nodes[zpp].color = RED;
                z = zpp;
            } else {
                if (z == nodes[zp].left) {
                    z = zp;
                    rotate_right(z);
                    zp = nodes[z].parent;
                    zpp = nodes[zp].parent;
                }
                nodes[zp].color = BLACK;
                nodes[zpp].color = RED;
                rotate_left(zpp);
            }
        }
    }
    nodes[root].color = BLACK;
}

static void insert(int key) {
    int z = new_node(key);
    int y = 0;
    int x = root;

    while (x != 0) {
        y = x;
        if (key < nodes[x].key)
            x = nodes[x].left;
        else
            x = nodes[x].right;
    }

    nodes[z].parent = y;
    if (y == 0)
        root = z;
    else if (key < nodes[y].key)
        nodes[y].left = z;
    else
        nodes[y].right = z;

    insert_fixup(z);
}

static int search(int key) {
    int x = root;
    while (x != 0) {
        if (key == nodes[x].key) return 1;
        if (key < nodes[x].key)
            x = nodes[x].left;
        else
            x = nodes[x].right;
    }
    return 0;
}

static int tree_height(int x) {
    if (x == 0) return 0;
    int lh = tree_height(nodes[x].left);
    int rh = tree_height(nodes[x].right);
    return 1 + ((lh > rh) ? lh : rh);
}

int main(void) {
    unsigned int seed = 123456789;
    int i;
    long found = 0;

    init_tree();

    // Insert 499999 random keys
    for (i = 0; i < MAX_NODES - 1; i++) {
        seed = seed * 1664525u + 1013904223u;
        insert((int)(seed >> 4));
    }

    // Search for random keys
    seed = 987654321;
    for (i = 0; i < 1000000; i++) {
        seed = seed * 1664525u + 1013904223u;
        found += search((int)(seed >> 4));
    }

    int height = tree_height(root);
    long result = found * 31 + height;
    return (int)(result % 256);
}
