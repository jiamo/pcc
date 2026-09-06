# Investigation: installed pcc1 helper imports can use the caller checkout

## Status
resolved

## Problem Description
The stable toolchain must consume its copied helper sources while compiling
applications from arbitrary working directories. Its launcher set PYTHONPATH,
but Python's current-directory entry still preceded that source root. The
frontend's helper payload only inserted a source root absent from sys.path,
so the launcher's existing entry prevented that correction.

## Repro
From `/Users/jiamo/my/pcc`, run the installed v84 host interpreter with the
launcher's PYTHONPATH and the self-backend helper's source-path prologue, then
inspect `importlib.util.find_spec('pcc').origin`. The source was already in
sys.path, but the origin was `/Users/jiamo/my/pcc/pcc/__init__.py`.

## Test [CONFIRMED]
The read-only interpreter probe above returned the mutable checkout before
the change. `test_installed_helper_imports_ignore_caller_checkout_and_keep_app_environment`
now executes the generated launcher with an actual host interpreter and a
shadowing caller module; it requires the installed module, unchanged cwd,
VIRTUAL_ENV and PCC_PACKAGE_SITE.

## Proposals
- No.1 Disable Python's implicit unsafe path at the installed boundary [CONFIRMED]

## No.1 Disable Python's implicit unsafe path at the installed boundary
### Code Change
The generated launcher exports PYTHONSAFEPATH=1. It retains the copied-source
PYTHONPATH and the application's cwd and package-environment overrides.
### CONFIRMED
The focused generated-launcher execution regression passes. No native compiler
source or pre-existing installation was changed.

## Report
This repairs future launcher generation. The already-installed historical v84
launcher remains unchanged; this is not a new installation, promotion, release,
or package-execution qualification. Work is tracked in pcc issue #186.
