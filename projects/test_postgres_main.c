#include "postgres_fe.h"

#include "libpq-fe.h"

int
main(void)
{
    PQconninfoOption *opts;
    PQconninfoOption *opt;
    char *errmsg;
    int saw_host;
    int saw_port;
    int saw_dbname;

    errmsg = NULL;
    saw_host = 0;
    saw_port = 0;
    saw_dbname = 0;

    opts = PQconninfoParse(
        "host=localhost port=5432 dbname=pcc user=postgres",
        &errmsg
    );
    if (opts == NULL)
    {
        fprintf(stderr, "PQconninfoParse failed: %s\n", errmsg ? errmsg : "(null)");
        if (errmsg != NULL)
            PQfreemem(errmsg);
        return 1;
    }

    for (opt = opts; opt->keyword != NULL; opt++)
    {
        if (opt->val == NULL)
            continue;
        if (strcmp(opt->keyword, "host") == 0 && strcmp(opt->val, "localhost") == 0)
            saw_host = 1;
        if (strcmp(opt->keyword, "port") == 0 && strcmp(opt->val, "5432") == 0)
            saw_port = 1;
        if (strcmp(opt->keyword, "dbname") == 0 && strcmp(opt->val, "pcc") == 0)
            saw_dbname = 1;
    }

    PQconninfoFree(opts);

    printf("libpq version %d\n", PQlibVersion());
    printf(
        "conninfo: host=%d port=%d dbname=%d\n",
        saw_host,
        saw_port,
        saw_dbname
    );

    if (!saw_host || !saw_port || !saw_dbname)
    {
        fprintf(stderr, "PQconninfoParse lost expected values\n");
        return 2;
    }

    puts("OK");
    return 0;
}
