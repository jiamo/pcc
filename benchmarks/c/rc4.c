// RC4 stream cipher benchmark
// Stresses: byte-level operations, array swaps, table lookup, crypto patterns

#include <string.h>

struct RC4State {
    unsigned char S[256];
    int i, j;
};

static void rc4_init(struct RC4State *state, const unsigned char *key, int keylen) {
    int i, j;
    for (i = 0; i < 256; i++)
        state->S[i] = (unsigned char)i;

    j = 0;
    for (i = 0; i < 256; i++) {
        j = (j + state->S[i] + key[i % keylen]) & 0xFF;
        unsigned char tmp = state->S[i];
        state->S[i] = state->S[j];
        state->S[j] = tmp;
    }
    state->i = 0;
    state->j = 0;
}

static unsigned char rc4_byte(struct RC4State *state) {
    state->i = (state->i + 1) & 0xFF;
    state->j = (state->j + state->S[state->i]) & 0xFF;
    unsigned char tmp = state->S[state->i];
    state->S[state->i] = state->S[state->j];
    state->S[state->j] = tmp;
    return state->S[(state->S[state->i] + state->S[state->j]) & 0xFF];
}

static void rc4_crypt(struct RC4State *state, unsigned char *data, int len) {
    int i;
    for (i = 0; i < len; i++)
        data[i] ^= rc4_byte(state);
}

int main(void) {
    struct RC4State state;
    unsigned char key[16] = "benchmarkkey123";
    unsigned char data[4096];
    int i;
    unsigned int checksum = 0;

    for (int iter = 0; iter < 100000; iter++) {
        // Initialize data
        for (i = 0; i < 4096; i++)
            data[i] = (unsigned char)((iter + i * 7) & 0xFF);

        // Encrypt
        rc4_init(&state, key, 16);
        rc4_crypt(&state, data, 4096);

        // Decrypt
        rc4_init(&state, key, 16);
        rc4_crypt(&state, data, 4096);

        // Verify a byte and accumulate
        checksum += data[iter % 4096];
    }

    return (int)(checksum % 256);
}
