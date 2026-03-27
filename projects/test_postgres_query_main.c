#include "postgres_fe.h"

#include "libpq-fe.h"

int
main(int argc, char **argv)
{
    const char *conninfo;
    PGconn *conn;
    PGresult *res;
    const char *insert_params1[2];
    const char *insert_params2[2];
    const char *update_params[2];
    const char *select_params[1];
    const char *score_value;
    const char *server_version_num;
    const char *sum_value;
    const char *temp_rows;

    if (argc != 2)
    {
        fprintf(stderr, "usage: %s CONNINFO\n", argv[0]);
        return 2;
    }

    conninfo = argv[1];
    conn = PQconnectdb(conninfo);
    if (PQstatus(conn) != CONNECTION_OK)
    {
        fprintf(stderr, "PQconnectdb failed: %s\n", PQerrorMessage(conn));
        PQfinish(conn);
        return 1;
    }

    res = PQexec(conn, "SHOW server_version_num");
    if (PQresultStatus(res) != PGRES_TUPLES_OK || PQntuples(res) != 1)
    {
        fprintf(stderr, "SHOW server_version_num failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    server_version_num = PQgetvalue(res, 0, 0);
    printf("server_version_num=%s\n", server_version_num);
    PQclear(res);

    res = PQexec(conn, "CREATE TEMP TABLE pcc_smoke(name text, score int)");
    if (PQresultStatus(res) != PGRES_COMMAND_OK)
    {
        fprintf(stderr, "CREATE TEMP TABLE failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    insert_params1[0] = "alpha";
    insert_params1[1] = "7";
    res = PQexecParams(
        conn,
        "INSERT INTO pcc_smoke(name, score) VALUES ($1, $2)",
        2,
        NULL,
        insert_params1,
        NULL,
        NULL,
        0
    );
    if (PQresultStatus(res) != PGRES_COMMAND_OK)
    {
        fprintf(stderr, "INSERT alpha failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    if (strcmp(PQcmdTuples(res), "1") != 0)
    {
        fprintf(stderr, "unexpected alpha insert row count: %s\n", PQcmdTuples(res));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    res = PQprepare(
        conn,
        "pcc_insert",
        "INSERT INTO pcc_smoke(name, score) VALUES ($1, $2)",
        2,
        NULL
    );
    if (PQresultStatus(res) != PGRES_COMMAND_OK)
    {
        fprintf(stderr, "PQprepare failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    insert_params2[0] = "beta";
    insert_params2[1] = "35";
    res = PQexecPrepared(conn, "pcc_insert", 2, insert_params2, NULL, NULL, 0);
    if (PQresultStatus(res) != PGRES_COMMAND_OK)
    {
        fprintf(stderr, "INSERT beta failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    if (strcmp(PQcmdTuples(res), "1") != 0)
    {
        fprintf(stderr, "unexpected beta insert row count: %s\n", PQcmdTuples(res));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    update_params[0] = "10";
    update_params[1] = "alpha";
    res = PQexecParams(
        conn,
        "UPDATE pcc_smoke SET score = score + $1::int WHERE name = $2",
        2,
        NULL,
        update_params,
        NULL,
        NULL,
        0
    );
    if (PQresultStatus(res) != PGRES_COMMAND_OK)
    {
        fprintf(stderr, "UPDATE failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    if (strcmp(PQcmdTuples(res), "1") != 0)
    {
        fprintf(stderr, "unexpected update row count: %s\n", PQcmdTuples(res));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    select_params[0] = "alpha";
    res = PQexecParams(
        conn,
        "SELECT score FROM pcc_smoke WHERE name = $1",
        1,
        NULL,
        select_params,
        NULL,
        NULL,
        0
    );
    if (PQresultStatus(res) != PGRES_TUPLES_OK || PQntuples(res) != 1)
    {
        fprintf(stderr, "SELECT score failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    score_value = PQgetvalue(res, 0, 0);
    printf("alpha_score=%s\n", score_value);
    if (strcmp(score_value, "17") != 0)
    {
        fprintf(stderr, "unexpected alpha score: %s\n", score_value);
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    res = PQexec(conn, "BEGIN");
    if (PQresultStatus(res) != PGRES_COMMAND_OK)
    {
        fprintf(stderr, "BEGIN failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    res = PQexec(conn, "INSERT INTO pcc_smoke(name, score) VALUES ('temp', 99)");
    if (PQresultStatus(res) != PGRES_COMMAND_OK)
    {
        fprintf(stderr, "INSERT temp failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    res = PQexec(conn, "ROLLBACK");
    if (PQresultStatus(res) != PGRES_COMMAND_OK)
    {
        fprintf(stderr, "ROLLBACK failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    res = PQexec(conn, "SELECT count(*), sum(score) FROM pcc_smoke");
    if (PQresultStatus(res) != PGRES_TUPLES_OK || PQntuples(res) != 1)
    {
        fprintf(stderr, "SELECT aggregate failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }

    if (strcmp(PQgetvalue(res, 0, 0), "2") != 0)
    {
        fprintf(stderr, "unexpected row count: %s\n", PQgetvalue(res, 0, 0));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    sum_value = PQgetvalue(res, 0, 1);
    printf("sum=%s\n", sum_value);

    if (strcmp(sum_value, "52") != 0)
    {
        fprintf(stderr, "unexpected sum: %s\n", sum_value);
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    res = PQexec(conn, "SELECT count(*) FROM pcc_smoke WHERE name = 'temp'");
    if (PQresultStatus(res) != PGRES_TUPLES_OK || PQntuples(res) != 1)
    {
        fprintf(stderr, "SELECT temp rows failed: %s\n", PQerrorMessage(conn));
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    temp_rows = PQgetvalue(res, 0, 0);
    printf("temp_rows=%s\n", temp_rows);
    if (strcmp(temp_rows, "0") != 0)
    {
        fprintf(stderr, "unexpected temp row count: %s\n", temp_rows);
        PQclear(res);
        PQfinish(conn);
        return 1;
    }
    PQclear(res);

    PQfinish(conn);
    puts("OK");
    return 0;
}
