#include <stdio.h>
#include <string.h>

#include "readline-8.2/history.h"
#include "readline-8.2/readline.h"

char PC = 0;
char *BC = 0;
char *UP = 0;
short ospeed = 0;

int tgetent(char *bp, char *name) {
    (void)bp;
    (void)name;
    return 1;
}

int tgetflag(char *id) {
    (void)id;
    return 0;
}

int tgetnum(char *id) {
    if (id && id[0] == 'c' && id[1] == 'o' && id[2] == 0)
        return 80;
    if (id && id[0] == 'l' && id[1] == 'i' && id[2] == 0)
        return 24;
    return -1;
}

char *tgetstr(char *id, char **area) {
    (void)id;
    (void)area;
    return 0;
}

int tputs(char *str, int affcnt, int (*putc_fn)(int)) {
    (void)affcnt;
    if (str == 0 || putc_fn == 0)
        return 0;
    while (*str)
        putc_fn((unsigned char)*str++);
    return 0;
}

char *tgoto(char *cap, int col, int row) {
    (void)col;
    (void)row;
    return cap ? cap : "";
}

int main(void) {
    HIST_ENTRY *entry;

    using_history();
    add_history("hello");
    add_history("readline");

    entry = history_get(history_base + 1);
    if (entry == 0 || strcmp(entry->line, "readline") != 0) {
        printf("history_get failed\n");
        return 1;
    }
    if (history_length != 2) {
        printf("unexpected history length: %d\n", history_length);
        return 1;
    }
    if (rl_readline_version <= 0 || rl_library_version == 0) {
        printf("readline version globals missing\n");
        return 1;
    }

    printf("readline version %s (%d)\n", rl_library_version, rl_readline_version);
    printf("history entry: %s\n", entry->line);
    printf("OK\n");
    return 0;
}
