// State machine benchmark (CoreMark-inspired)
// Validates if input streams contain well-formed numbers
// Stresses: branch prediction, switch statements, state transitions, character classification

#include <string.h>

#define STREAM_SIZE 1024
#define NUM_STREAMS 500000

enum State {
    START, SIGN, INTEGER, DOT, FRACTION, E, E_SIGN, EXPONENT, ACCEPT, REJECT
};

static int is_digit(char c) { return c >= '0' && c <= '9'; }

static enum State transition(enum State state, char c) {
    switch (state) {
        case START:
            if (c == '+' || c == '-') return SIGN;
            if (is_digit(c)) return INTEGER;
            if (c == '.') return DOT;
            return REJECT;
        case SIGN:
            if (is_digit(c)) return INTEGER;
            if (c == '.') return DOT;
            return REJECT;
        case INTEGER:
            if (is_digit(c)) return INTEGER;
            if (c == '.') return FRACTION;
            if (c == 'e' || c == 'E') return E;
            if (c == '\0') return ACCEPT;
            return REJECT;
        case DOT:
            if (is_digit(c)) return FRACTION;
            return REJECT;
        case FRACTION:
            if (is_digit(c)) return FRACTION;
            if (c == 'e' || c == 'E') return E;
            if (c == '\0') return ACCEPT;
            return REJECT;
        case E:
            if (c == '+' || c == '-') return E_SIGN;
            if (is_digit(c)) return EXPONENT;
            return REJECT;
        case E_SIGN:
            if (is_digit(c)) return EXPONENT;
            return REJECT;
        case EXPONENT:
            if (is_digit(c)) return EXPONENT;
            if (c == '\0') return ACCEPT;
            return REJECT;
        default:
            return REJECT;
    }
}

static int validate_number(const char *str, int len) {
    enum State state = START;
    int i;
    for (i = 0; i < len; i++) {
        state = transition(state, str[i]);
        if (state == REJECT) return 0;
    }
    state = transition(state, '\0');
    return (state == ACCEPT) ? 1 : 0;
}

int main(void) {
    char stream[STREAM_SIZE];
    unsigned int seed = 55555;
    long valid_count = 0;
    long total_chars = 0;

    for (int iter = 0; iter < NUM_STREAMS; iter++) {
        int len = 5 + (iter % 50);
        int i;

        // Generate pseudo-random potential number strings
        seed = seed * 1103515245u + 12345u;
        int style = (seed >> 16) % 8;

        int pos = 0;
        switch (style) {
            case 0: // valid integer
                if ((seed >> 8) & 1) stream[pos++] = '-';
                for (i = 0; i < 3 + (len % 5); i++) {
                    seed = seed * 1103515245u + 12345u;
                    stream[pos++] = '0' + (seed >> 16) % 10;
                }
                break;
            case 1: // valid float
                if ((seed >> 8) & 1) stream[pos++] = '+';
                for (i = 0; i < 2; i++) {
                    seed = seed * 1103515245u + 12345u;
                    stream[pos++] = '0' + (seed >> 16) % 10;
                }
                stream[pos++] = '.';
                for (i = 0; i < 3; i++) {
                    seed = seed * 1103515245u + 12345u;
                    stream[pos++] = '0' + (seed >> 16) % 10;
                }
                break;
            case 2: // valid scientific
                stream[pos++] = '1';
                stream[pos++] = '.';
                stream[pos++] = '5';
                stream[pos++] = 'e';
                stream[pos++] = '+';
                stream[pos++] = '1';
                stream[pos++] = '0';
                break;
            default: // random chars (likely invalid)
                for (i = 0; i < len && pos < STREAM_SIZE - 1; i++) {
                    seed = seed * 1103515245u + 12345u;
                    stream[pos++] = 32 + (seed >> 16) % 96;
                }
                break;
        }
        if (pos >= STREAM_SIZE) pos = STREAM_SIZE - 1;

        valid_count += validate_number(stream, pos);
        total_chars += pos;
    }

    long result = valid_count * 1000 + (total_chars >> 8);
    return (int)(result % 256);
}
