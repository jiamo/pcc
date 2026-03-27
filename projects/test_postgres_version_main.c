#include "postgres_fe.h"

#include "libpq-fe.h"

int
main(void)
{
    int version;

    version = PQlibVersion();
    printf("libpq version %d\n", version);

    if (version != 170004)
        return 1;

    puts("OK");
    return 0;
}
