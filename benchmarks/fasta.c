// FASTA sequence generation benchmark
// From the Computer Language Benchmarks Game
// Stresses: floating point, table lookup, modular arithmetic, function calls

#define IM 139968
#define IA 3877
#define IC 29573

static int last_random = 42;

static double gen_random(double max_val) {
    last_random = (last_random * IA + IC) % IM;
    return max_val * last_random / IM;
}

struct AminoAcid {
    char c;
    double p;
};

static struct AminoAcid iub[] = {
    {'a', 0.27}, {'c', 0.12}, {'g', 0.12}, {'t', 0.27},
    {'B', 0.02}, {'D', 0.02}, {'H', 0.02}, {'K', 0.02},
    {'M', 0.02}, {'N', 0.02}, {'R', 0.02}, {'S', 0.02},
    {'V', 0.02}, {'W', 0.02}, {'Y', 0.02},
};
static const int IUB_LEN = 15;

static struct AminoAcid homosapiens[] = {
    {'a', 0.3029549426680}, {'c', 0.1979883004921},
    {'g', 0.1975473066391}, {'t', 0.3015094502008},
};
static const int HS_LEN = 4;

static void make_cumulative(struct AminoAcid *table, int len) {
    double cp = 0.0;
    int i;
    for (i = 0; i < len; i++) {
        cp += table[i].p;
        table[i].p = cp;
    }
}

static char select_random(struct AminoAcid *table, int len) {
    double r = gen_random(1.0);
    int i;
    for (i = 0; i < len - 1; i++) {
        if (r < table[i].p) return table[i].c;
    }
    return table[len - 1].c;
}

static long checksum;

static void make_random_fasta(struct AminoAcid *table, int len, int n) {
    int i;
    for (i = 0; i < n; i++) {
        char c = select_random(table, len);
        checksum += c * (i & 0xFF);
    }
}

static void make_repeat_fasta(const char *alu, int alu_len, int n) {
    int i;
    for (i = 0; i < n; i++) {
        char c = alu[i % alu_len];
        checksum += c * (i & 0xFF);
    }
}

int main(void) {
    static const char alu[] =
        "GGCCGGGCGCGGTGGCTCACGCCTGTAATCCCAGCACTTTGG"
        "GAGGCCGAGGCGGGCGGATCACCTGAGGTCAGGAGTTCGAGA"
        "CCAGCCTGGCCAACATGGTGAAACCCCGTCTCTACTAAAAATA"
        "CAAAAATTAGCCGGGCGTGGTGGCGCGCGCCTGTAATCCCAG"
        "CTACTCGGGAGGCTGAGGCAGGAGAATCGCTTGAACCCGGGA"
        "GGCGGAGGTTGCAGTGAGCCGAGATCGCGCCACTGCACTCCA"
        "GCCTGGGCGACAGAGCGAGACTCCGTCTCAAAAA";
    int alu_len = sizeof(alu) - 1;
    int n = 5000000;

    checksum = 0;
    make_cumulative(iub, IUB_LEN);
    make_cumulative(homosapiens, HS_LEN);

    make_repeat_fasta(alu, alu_len, n * 2);
    make_random_fasta(iub, IUB_LEN, n * 3);
    make_random_fasta(homosapiens, HS_LEN, n * 5);

    return (int)((checksum >> 8) % 256);
}
