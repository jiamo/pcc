// Linked list benchmark - mergesort on linked list
// Stresses: pointer chasing, cache misses, allocation patterns, recursion

#define N 500000

struct Node {
    int val;
    int next; // index-based "pointer", -1 = null
};

static struct Node pool[N];
static int head;

static int list_len(int h) {
    int len = 0;
    while (h != -1) { len++; h = pool[h].next; }
    return len;
}

static void split(int source, int *front, int *back) {
    int slow = source;
    int fast = pool[source].next;

    while (fast != -1) {
        fast = pool[fast].next;
        if (fast != -1) {
            slow = pool[slow].next;
            fast = pool[fast].next;
        }
    }

    *front = source;
    *back = pool[slow].next;
    pool[slow].next = -1;
}

static int merge(int a, int b) {
    if (a == -1) return b;
    if (b == -1) return a;

    int result;
    if (pool[a].val <= pool[b].val) {
        result = a;
        pool[a].next = merge(pool[a].next, b);
    } else {
        result = b;
        pool[b].next = merge(a, pool[b].next);
    }
    return result;
}

// Iterative merge sort to avoid stack overflow
static int mergesort_iterative(int h) {
    if (h == -1 || pool[h].next == -1) return h;

    // Bottom-up merge sort
    int width, n = list_len(h);

    for (width = 1; width < n; width *= 2) {
        int dummy_next = -1;
        int *tail = &dummy_next;
        int curr = h;

        while (curr != -1) {
            int left = curr;
            int right = curr;

            // Advance right by width steps
            int i;
            for (i = 0; i < width - 1 && right != -1; i++)
                right = pool[right].next;

            if (right == -1) {
                *tail = left;
                break;
            }

            int next = pool[right].next;
            pool[right].next = -1;
            right = next;

            // Find end of right portion
            int right_end = right;
            for (i = 0; i < width - 1 && right_end != -1 && pool[right_end].next != -1; i++)
                right_end = pool[right_end].next;

            if (right_end != -1) {
                next = pool[right_end].next;
                pool[right_end].next = -1;
            } else {
                next = -1;
            }

            // Merge left and right
            int merged = merge(left, right);
            *tail = merged;
            while (*tail != -1 && pool[*tail].next != -1)
                tail = &pool[*tail].next;
            if (*tail != -1)
                tail = &pool[*tail].next;

            curr = next;
        }

        h = dummy_next;
    }
    return h;
}

int main(void) {
    unsigned int seed = 1337;
    int i;
    long checksum = 0;

    // Build linked list with random values
    for (i = 0; i < N; i++) {
        seed = seed * 1664525u + 1013904223u;
        pool[i].val = (int)(seed >> 4);
        pool[i].next = (i + 1 < N) ? i + 1 : -1;
    }
    head = 0;

    // Sort
    head = mergesort_iterative(head);

    // Verify sorted order and compute checksum
    int curr = head;
    int prev_val = pool[curr].val;
    int sorted = 1;
    while (curr != -1) {
        if (pool[curr].val < prev_val) sorted = 0;
        checksum += pool[curr].val & 0xFF;
        prev_val = pool[curr].val;
        curr = pool[curr].next;
    }

    return (int)((checksum + sorted) % 256);
}
