# Gateway asynchronous DNS — focused evidence

Date: 2026-08-14

Task: `GATEWAY-P2-ASYNC-DNS`

The fail-fast non-integration DNS gate completed with `24 passed, 1
deselected`. A source-contract assertion was narrowed from rejecting the word
`getaddrinfo` in documentation to rejecting executable `getaddrinfo(` calls;
the production resolver remains nonblocking and does not call the host
resolver.

The source-current pcc1 self/no-libpython live resolver/proxy gate and the
named platform-policy boundaries remain open, so this task is `DONE_WEAK`.
