// Simple regex NFA matching benchmark
// Stresses: state machine, backtracking, string processing, bit sets

#include <string.h>

// Simple NFA regex engine supporting: . * + ? | () []
// We implement a Thompson NFA for patterns like (a|b)*abb

#define MAX_STATES 256
#define MAX_TEXT 1024

struct State {
    int c;           // character to match, -1 = epsilon, -2 = any (.)
    int out1;        // transition 1 (-1 = no transition)
    int out2;        // transition 2 (for splits)
    int accept;      // is this an accept state?
};

static struct State nfa[MAX_STATES];
static int nstate;
static unsigned int clist[MAX_STATES]; // current state set
static unsigned int nlist[MAX_STATES]; // next state set
static int clen, nlen;

static int new_state(int c, int out1, int out2, int accept) {
    int s = nstate++;
    nfa[s].c = c;
    nfa[s].out1 = out1;
    nfa[s].out2 = out2;
    nfa[s].accept = accept;
    return s;
}

static char visited[MAX_STATES];

static void add_state(unsigned int *list, int *len, int s) {
    if (s < 0 || visited[s]) return;
    visited[s] = 1;
    if (nfa[s].c == -1) { // epsilon
        add_state(list, len, nfa[s].out1);
        add_state(list, len, nfa[s].out2);
        return;
    }
    list[(*len)++] = s;
}

static int nfa_match(const char *text, int len) {
    int i, j;
    clen = 0;
    memset(visited, 0, sizeof(visited));
    add_state(clist, &clen, 0); // start state

    for (i = 0; i < len; i++) {
        nlen = 0;
        memset(visited, 0, sizeof(visited));
        for (j = 0; j < clen; j++) {
            int s = clist[j];
            int c = nfa[s].c;
            if (c == -2 || c == (unsigned char)text[i]) {
                add_state(nlist, &nlen, nfa[s].out1);
            }
        }
        // Swap lists
        int tlen = clen;
        clen = nlen;
        nlen = tlen;
        for (j = 0; j < clen; j++) clist[j] = nlist[j];
    }

    // Check if any current state is accepting
    for (j = 0; j < clen; j++) {
        if (nfa[clist[j]].accept) return 1;
    }
    return 0;
}

// Build NFA for pattern: (a|b|c|d)*abcd(a|b|c|d)*
static void build_pattern(void) {
    nstate = 0;

    // State 0: split -> match a/b/c/d or start matching "abcd"
    // States 0-1: initial (a|b|c|d)* loop
    int s0 = new_state(-1, 1, 2, 0);     // split: loop or start matching
    int s1 = new_state(-2, 0, -1, 0);    // match any -> back to s0

    // States 2-5: match "abcd"
    int s2 = new_state('a', 3, -1, 0);
    int s3 = new_state('b', 4, -1, 0);
    int s4 = new_state('c', 5, -1, 0);
    int s5 = new_state('d', 6, -1, 0);

    // States 6-7: trailing (a|b|c|d)*
    int s6 = new_state(-1, 7, 8, 0);     // split: loop or accept
    int s7 = new_state(-2, 6, -1, 0);    // match any -> back to s6
    int s8 = new_state(-1, -1, -1, 1);   // accept

    (void)s0; (void)s1; (void)s2; (void)s3;
    (void)s4; (void)s5; (void)s6; (void)s7; (void)s8;
}

int main(void) {
    build_pattern();

    unsigned int seed = 42424242;
    long matches = 0;
    char text[MAX_TEXT];
    int iter;

    for (iter = 0; iter < 2000000; iter++) {
        int len = 20 + (iter % 80);
        int i;
        for (i = 0; i < len; i++) {
            seed = seed * 1103515245u + 12345u;
            text[i] = 'a' + (seed >> 16) % 4;
        }
        matches += nfa_match(text, len);
    }

    return (int)(matches % 256);
}
