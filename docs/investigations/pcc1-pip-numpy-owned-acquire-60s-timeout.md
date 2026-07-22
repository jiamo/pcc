# Investigation: `pcc1 -m pip install numpy` owned acquisition fails with PCC-PKG-ACQUIRE-DOWNLOAD-FAILED (hard 60s libcurl timeout)

## Status
resolved

## Problem Description
Verifying the README "NumPy on pcc1" flow: step 2
(`build/bootstrap/pcc1 -m pip install numpy`) failed deterministically with
`"diagnostic": "PCC-PKG-ACQUIRE-DOWNLOAD-FAILED"` while resolution succeeded
(the Simple-Repository index page downloaded, the numpy 2.4.6 sdist URL and
sha256 were resolved, `transport_provider: pcc-runtime-libcurl`). System curl
from the same shell reached pypi fine.

## Repro
```bash
scripts/bootstrap.sh --stage 1
build/bootstrap/pcc1 -m pip install numpy   # ok:false, DOWNLOAD-FAILED (twice)
```

## Test [CONFIRMED]
Probe matrix through the runtime intrinsic `os._pcc_http_download_to_file`
(run via `pcc1 probe.py`, no `-o`):

- small index page → package-cache scratch dir: rc 0
- 20MB numpy sdist → /tmp: rc 0
- 20MB numpy sdist → package-cache scratch dir: rc **-14**, and the whole
  3-download probe took 1:54 — the failing transfer aborted at ~60s.

So the failure is not path-, TLS-, or resolver-related: `py_http.c`'s
`download_with_system_libcurl` set `CURLOPT_TIMEOUT = 60` (hard total-transfer
wall clock). A 20MB sdist at the observed ~0.4MB/s needs ~55–65s — exactly at
the cliff, so runs pass or fail with bandwidth jitter.

## Proposals
- No.1 Replace the fixed total-transfer timeout with libcurl stall abort   [CONFIRMED]

## No.1 Replace the fixed total-transfer timeout with libcurl stall abort
### Code Change
`pcc/py_runtime/src/py_http.c` (`OBJ_PY_CC_HELPERS`, no pcc-Python mirror):
drop `CURLOPT_TIMEOUT 60`; set `CURLOPT_LOW_SPEED_LIMIT = 1024` and
`CURLOPT_LOW_SPEED_TIME = 30` (abort only when under 1KiB/s for 30 consecutive
seconds). `CURLOPT_CONNECTTIMEOUT 20` stays.
### CONFIRMED
After `rm pcc/py_runtime/libpy_runtime*.a` + `scripts/bootstrap.sh --stage 1`:
`pcc1 -m pip install numpy` → `ok:true` (owned acquisition, sha256-verified,
installed into the per-user pcc-native environment, `links_libpython:false`);
`pcc1 np_demo.py -o np_demo && ./np_demo` prints `2.4.6` / `[2, 3, 4]`; the
same binary prints identical output under `PCC_GC_BACKEND=0..4`; `otool -L`
shows no libpython. `pcc1 np_demo.py` without `-o` (run-cache mode) works too.
Regression:
`tests/python/test_runtime_tripwires.py::test_owned_acquire_download_has_no_fixed_transfer_timeout`
locks the no-fixed-timeout policy. Commit-level gates:
`test_bootstrap_gate_baseline + fallback baselines` → 27 passed.

## Report
No.1 landed. Root-cause class: fixed wall-clock caps on unbounded-size
transfers; the correct failure signal for downloads is stall, not duration.
