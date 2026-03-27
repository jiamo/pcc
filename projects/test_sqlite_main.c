#include <stdio.h>
#include <string.h>
#include <errno.h>

#include "sqlite-amalgamation-3490100/sqlite3.h"

static int fail_sqlite(sqlite3 *db, sqlite3_stmt *stmt, char *errmsg, const char *msg) {
    if (msg) {
        printf("%s\n", msg);
    }
    if (errmsg) {
        printf("%s\n", errmsg);
        sqlite3_free(errmsg);
    }
    if (db) {
        printf("errcode: %d\n", sqlite3_errcode(db));
        printf("%s\n", sqlite3_errmsg(db));
        printf("extended errcode: %d\n", sqlite3_extended_errcode(db));
        printf("system errno: %d\n", sqlite3_system_errno(db));
    }
    printf("errno: %d\n", errno);
    if (stmt) {
        sqlite3_finalize(stmt);
    }
    if (db) {
        sqlite3_close(db);
    }
    return 1;
}

int main(int argc, char **argv) {
    sqlite3 *db = 0;
    sqlite3_stmt *stmt = 0;
    char *errmsg = 0;
    const unsigned char *text = 0;
    char selected_name[32];
    int count = 0;
    int sum = 0;
    int maxlen = 0;
    int score = 0;
    int world_score = 0;
    int updated_score = 0;
    int persisted_score = 0;
    sqlite3_int64 hello_rowid = 0;
    sqlite3_int64 world_rowid = 0;
    int insert_changes = 0;
    int update_changes = 0;
    const char *db_path = argc > 1 ? argv[1] : ":memory:";
    int rc = sqlite3_open(db_path, &db);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_open failed");
    }

    rc = sqlite3_exec(
        db,
        "drop table if exists t;"
        "create table t(id integer primary key, name text, score integer);",
        0,
        0,
        &errmsg
    );
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, errmsg, "sqlite3_exec(create) failed");
    }

    rc = sqlite3_prepare_v2(
        db,
        "insert into t(name, score) values(?, ?);",
        -1,
        &stmt,
        0
    );
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_prepare_v2(insert) failed");
    }

    rc = sqlite3_bind_text(stmt, 1, "hello", -1, SQLITE_STATIC);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_bind_text(insert hello) failed");
    }
    rc = sqlite3_bind_int(stmt, 2, 10);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_bind_int(insert hello) failed");
    }
    rc = sqlite3_step(stmt);
    if (rc != SQLITE_DONE) {
        printf("insert hello step rc: %d expected: %d\n", rc, SQLITE_DONE);
        return fail_sqlite(db, stmt, 0, "sqlite3_step(insert hello) failed");
    }
    hello_rowid = sqlite3_last_insert_rowid(db);
    insert_changes = sqlite3_changes(db);
    if (hello_rowid != 1 || insert_changes != 1) {
        return fail_sqlite(db, stmt, 0, "unexpected insert hello metadata");
    }
    rc = sqlite3_reset(stmt);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_reset(insert) failed");
    }
    rc = sqlite3_clear_bindings(stmt);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_clear_bindings(insert) failed");
    }

    rc = sqlite3_bind_text(stmt, 1, "world", -1, SQLITE_STATIC);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_bind_text(insert world) failed");
    }
    rc = sqlite3_bind_int(stmt, 2, 20);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_bind_int(insert world) failed");
    }
    rc = sqlite3_step(stmt);
    if (rc != SQLITE_DONE) {
        printf("insert world step rc: %d expected: %d\n", rc, SQLITE_DONE);
        return fail_sqlite(db, stmt, 0, "sqlite3_step(insert world) failed");
    }
    world_rowid = sqlite3_last_insert_rowid(db);
    insert_changes = sqlite3_changes(db);
    if (world_rowid != 2 || insert_changes != 1) {
        return fail_sqlite(db, stmt, 0, "unexpected insert world metadata");
    }
    sqlite3_finalize(stmt);
    stmt = 0;

    rc = sqlite3_exec(
        db,
        "update t set score = score + 7 where name = 'hello';",
        0,
        0,
        &errmsg
    );
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, errmsg, "sqlite3_exec(update) failed");
    }
    update_changes = sqlite3_changes(db);
    if (update_changes != 1) {
        return fail_sqlite(db, stmt, 0, "unexpected update metadata");
    }

    rc = sqlite3_exec(
        db,
        "begin;"
        "insert into t(name, score) values('temp', 99);"
        "rollback;",
        0,
        0,
        &errmsg
    );
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, errmsg, "sqlite3_exec(rollback probe) failed");
    }

    rc = sqlite3_prepare_v2(
        db,
        "select name, score from t where id = ?;",
        -1,
        &stmt,
        0
    );
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_prepare_v2(select row) failed");
    }

    rc = sqlite3_bind_int(stmt, 1, 2);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_bind_int(select row) failed");
    }

    rc = sqlite3_step(stmt);
    if (rc != SQLITE_ROW) {
        return fail_sqlite(db, stmt, 0, "sqlite3_step(select row) did not return SQLITE_ROW");
    }

    text = sqlite3_column_text(stmt, 0);
    world_score = sqlite3_column_int(stmt, 1);
    if (!text || strcmp((const char *)text, "world") != 0 || world_score != 20) {
        return fail_sqlite(db, stmt, 0, "unexpected selected row");
    }
    snprintf(selected_name, sizeof(selected_name), "%s", (const char *)text);
    rc = sqlite3_step(stmt);
    if (rc != SQLITE_DONE) {
        return fail_sqlite(db, stmt, 0, "sqlite3_step(select row tail) failed");
    }
    sqlite3_finalize(stmt);
    stmt = 0;

    rc = sqlite3_prepare_v2(
        db,
        "select count(*), sum(score), max(length(name)) from t;",
        -1,
        &stmt,
        0
    );
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_prepare_v2(aggregate) failed");
    }
    rc = sqlite3_step(stmt);
    if (rc != SQLITE_ROW) {
        return fail_sqlite(db, stmt, 0, "sqlite3_step(aggregate) did not return SQLITE_ROW");
    }
    count = sqlite3_column_int(stmt, 0);
    sum = sqlite3_column_int(stmt, 1);
    maxlen = sqlite3_column_int(stmt, 2);
    if (count != 2 || sum != 37 || maxlen != 5) {
        return fail_sqlite(db, stmt, 0, "unexpected aggregate result");
    }
    rc = sqlite3_step(stmt);
    if (rc != SQLITE_DONE) {
        return fail_sqlite(db, stmt, 0, "sqlite3_step(aggregate tail) failed");
    }
    sqlite3_finalize(stmt);
    stmt = 0;

    rc = sqlite3_prepare_v2(
        db,
        "select score from t where name = ?;",
        -1,
        &stmt,
        0
    );
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_prepare_v2(updated score) failed");
    }
    rc = sqlite3_bind_text(stmt, 1, "hello", -1, SQLITE_STATIC);
    if (rc != SQLITE_OK) {
        return fail_sqlite(db, stmt, 0, "sqlite3_bind_text(updated score) failed");
    }
    rc = sqlite3_step(stmt);
    if (rc != SQLITE_ROW) {
        return fail_sqlite(db, stmt, 0, "sqlite3_step(updated score) did not return SQLITE_ROW");
    }
    updated_score = sqlite3_column_int(stmt, 0);
    if (updated_score != 17) {
        return fail_sqlite(db, stmt, 0, "unexpected updated score");
    }
    sqlite3_finalize(stmt);
    stmt = 0;

    if (strcmp(db_path, ":memory:") != 0) {
        sqlite3_close(db);
        db = 0;

        rc = sqlite3_open(db_path, &db);
        if (rc != SQLITE_OK) {
            return fail_sqlite(db, stmt, 0, "sqlite3_open(reopen) failed");
        }

        rc = sqlite3_prepare_v2(
            db,
            "select score from t where id = 1;",
            -1,
            &stmt,
            0
        );
        if (rc != SQLITE_OK) {
            return fail_sqlite(db, stmt, 0, "sqlite3_prepare_v2(reopen) failed");
        }
        rc = sqlite3_step(stmt);
        if (rc != SQLITE_ROW) {
            return fail_sqlite(db, stmt, 0, "sqlite3_step(reopen) did not return SQLITE_ROW");
        }
        persisted_score = sqlite3_column_int(stmt, 0);
        if (persisted_score != 17) {
            return fail_sqlite(db, stmt, 0, "unexpected persisted score");
        }
        sqlite3_finalize(stmt);
        stmt = 0;
    }

    printf("sqlite version %s\n", sqlite3_libversion());
    printf("insert rowids: %lld %lld\n", (long long)hello_rowid, (long long)world_rowid);
    printf("changes: insert=%d update=%d\n", insert_changes, update_changes);
    printf("selected row: %s %d\n", selected_name, world_score);
    printf("aggregate: count=%d sum=%d maxlen=%d\n", count, sum, maxlen);
    printf("updated score: %d\n", updated_score);
    if (strcmp(db_path, ":memory:") != 0) {
        printf("persisted score: %d\n", persisted_score);
    }
    printf("OK\n");

    if (stmt) {
        sqlite3_finalize(stmt);
    }
    sqlite3_close(db);
    return 0;
}
