#include <stdio.h>

#include "sqlite-amalgamation-3490100/sqlite3.h"

static void dump_state(const char *label, sqlite3 *db) {
    if (!db) {
        printf("STATE %s db=NULL\n", label);
    } else {
        printf(
            "STATE %s interrupted=%d errmsg=%s\n",
            label,
            sqlite3_is_interrupted(db),
            sqlite3_errmsg(db)
        );
    }
    fflush(stdout);
}

static int run_sql(sqlite3 *db, const char *label, const char *sql) {
    int rc;
    dump_state("before", db);
    printf("RUN %s\n", label);
    fflush(stdout);
    rc = sqlite3_exec(db, sql, 0, 0, 0);
    printf("DONE %s rc=%d\n", label, rc);
    fflush(stdout);
    dump_state("after", db);
    return rc;
}

int main(void) {
    sqlite3 *db = 0;
    sqlite3_stmt *stmt = 0;
    int rc;

    printf("OPEN\n");
    fflush(stdout);
    rc = sqlite3_open(":memory:", &db);
    printf("OPEN rc=%d\n", rc);
    fflush(stdout);
    if (rc != SQLITE_OK) return 1;
    dump_state("post-open", db);

    rc = run_sql(db, "create", "create table t(id integer primary key, name text);");
    if (rc != SQLITE_OK) return 2;

    rc = run_sql(db, "insert", "insert into t(name) values('hello'),('world');");
    if (rc != SQLITE_OK) return 3;

    printf("PREPARE\n");
    fflush(stdout);
    rc = sqlite3_prepare_v2(db, "select name from t where id = 2;", -1, &stmt, 0);
    printf("PREPARE rc=%d\n", rc);
    fflush(stdout);
    dump_state("post-prepare", db);
    if (rc != SQLITE_OK) return 4;

    printf("STEP\n");
    fflush(stdout);
    rc = sqlite3_step(stmt);
    printf("STEP rc=%d\n", rc);
    fflush(stdout);
    dump_state("post-step", db);

    sqlite3_finalize(stmt);
    sqlite3_close(db);
    return 0;
}
