# Scalar-call confident fast path denied

## Proposal and sizing

Recognize only the compiler's canonical simple call shape before `_CALL_RE`:
simple scalar return, explicit non-nested signature, ASCII destination/callee,
non-parenthesized arguments, and the existing attribute/metadata suffix. Every
other shape falls through unchanged.

Across the frozen Stage2 corpus, normalized extracted fields were identical to
the regex groups:

```text
call instructions     2,678,736
eligible fast path    2,678,616  (99.9955%)
field mismatches              0
regex fallback              120  (aggregate-return calls)
```

A self-contained pcc1-compiled parser prefilter produced identical `8950000`
output and measured 4.51s / 69.641B instructions for the full regex versus
1.20s / 17.809B for the scalar extraction. That 3.76x isolated result
authorized one Stage1 build. Focused tests passed 8/8, strict parser closure
passed, and the standard host emit worker produced exact item311 assembly.

## Source-frozen compilers

Control and candidate use CPython 3.15.0rc1, GC0, self/no-libpython, one runtime
archive (`624e1de9...`), and libSystem-only linkage. Their 1,137-file source
manifests differ only in `pcc/backend/self_backend_parse.py`.

```text
                         control                           candidate
source     00f912fc97ad19257a96cf73c5f1ea5bb...  e77dd67f164dadea0be4a7a82b34f38c...
parser     809341afa02de5d5c42c6c64d90e6acb...  b8d10dfacd9c21ae6de1d7ac85bc9d1...
pcc1       ebde05bbdf2bf0caf47e1f15421de7d5...  faa4f6de5f5c71e44ad222d3a2fc3e4...
```

Single Stage1 construction receipts were 274.56s / 177.341B instructions and
263.75s / 177.373B. They are not a paired Stage1 speedup claim.

## Item311 verdict

After balanced warmups, three B/C, C/B, B/C pairs produced:

```text
pair   wall B/C   CPU B/C   instructions C/B   footprint C/B   assembly
1       1.04155    1.04164        0.96464          0.86805      ff943e10...
2       1.03247    1.03329        0.96533          0.86806      ff943e10...
3       1.04218    1.04227        0.96491          0.86801      ff943e10...
median  1.04155    1.04164        0.96491          0.86805      identical
```

This is a stable real improvement: about 4.16% wall/CPU, 3.51% instructions,
and 13.2% footprint. It nevertheless misses the explicitly retained 1.05x
median wall/CPU line. The threshold was not moved after seeing the result, so
the proposal is denied and no Stage2 ran.

The profile confirms deletion rather than relocation: regex pattern work falls
5.17% -> 1.07%, while the full parser falls 54.08% -> 45.65%; unlike the prior
full-fallback proposal, type-prefix parsing does not grow. Even so, the 100-line
fast path does not clear its registered retention bar.

The candidate and its temporary test were removed by forward patch. The parser
is byte-identical to accepted No.72 SHA-256
`809341afa02de5d5c42c6c64d90e6acb9f4dbaa05076d8380c370f2bc7d608b6`.
Raw artifacts are under `build/scalar-call-*`.

