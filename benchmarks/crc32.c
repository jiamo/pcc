// CRC-32 computation benchmark
// Stresses: table lookup, bit manipulation, loop throughput

static unsigned int crc_table[256];

static void init_crc_table(void) {
    unsigned int c;
    int n, k;
    for (n = 0; n < 256; n++) {
        c = (unsigned int)n;
        for (k = 0; k < 8; k++) {
            if (c & 1)
                c = 0xEDB88320u ^ (c >> 1);
            else
                c = c >> 1;
        }
        crc_table[n] = c;
    }
}

static unsigned int crc32(unsigned int crc, const unsigned char *buf, int len) {
    crc = crc ^ 0xFFFFFFFFu;
    int i;
    for (i = 0; i < len; i++)
        crc = crc_table[(crc ^ buf[i]) & 0xFF] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFu;
}

int main(void) {
    unsigned char data[8192];
    int i;
    unsigned int result = 0;

    init_crc_table();

    // Generate data and compute CRC many times
    for (i = 0; i < 8192; i++)
        data[i] = (unsigned char)(i * 31 + 17);

    for (i = 0; i < 200000; i++) {
        data[0] = (unsigned char)(i & 0xFF);
        data[1] = (unsigned char)((i >> 8) & 0xFF);
        result ^= crc32(result, data, 8192);
    }

    return (int)(result % 256);
}
