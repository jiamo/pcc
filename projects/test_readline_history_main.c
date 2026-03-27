#include <string.h>
#include <stdio.h>

typedef void *histdata_t;

typedef struct _hist_entry {
    char *line;
    char *timestamp;
    histdata_t data;
} HIST_ENTRY;

extern int history_base;
extern int history_length;
extern void using_history(void);
extern void add_history(const char *);
extern HIST_ENTRY *history_get(int);

int main(void) {
    HIST_ENTRY *entry;

    using_history();
    add_history("hello");
    add_history("history");

    entry = history_get(history_base + 1);
    if (entry == 0 || strcmp(entry->line, "history") != 0) {
        printf("history_get failed\n");
        return 1;
    }
    if (history_length != 2) {
        printf("unexpected history length: %d\n", history_length);
        return 1;
    }

    printf("history entry: %s\n", entry->line);
    printf("OK\n");
    return 0;
}
