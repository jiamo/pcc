# Investigation: pproxy HTTP listener resets Claude Code gateway requests

## Status
resolved

## Problem Description
Claude Code 2.1.258 must reach OpenRouter through the user's existing local
pproxy route:

```text
Claude Code -> local HTTP proxy -> SOCKS relay t-arm1:8084 -> OpenRouter
```

The existing listener is started as:

```bash
uv run python -m pproxy -l http+socks5://:8082 -r socks://t-arm1:8084
```

Raw Anthropic Messages requests work through this route, but `claude -p`
returns `Connection dropped (ECONNRESET)`. The repair must be generic: no
Claude, OpenRouter, hostname, or credential special case, and the user's
running 8082 process must remain untouched while alternate ports are tested.

### Update 2026-09-02: fragmented HTTP prefix defect confirmed

`HTTP.guess()` used `StreamReader.read(4)` and assumed that TCP returned all
four bytes. A deterministic `C` then delayed `ONN...` feed returned only
`b"C"`, so the mixed HTTP/SOCKS listener rejected the connection. Replacing
that with an unconditional `readexactly(4)` would deadlock SOCKS5: its initial
greeting is three bytes and the client waits for the server's selection reply.

The generic fix reads one byte first. A byte that cannot begin a supported
HTTP method is rolled back immediately; an HTTP initial reads the remaining
three bytes exactly. Focused tests cover fragmented CONNECT and the three-byte
SOCKS5 control. A live modified listener on 8084 then passed fragmented
CONNECT + TLS + HTTP 200, ordinary HTTP-proxy HTTP 200, and SOCKS5 HTTP 200,
all through `socks://t-arm1:8084`.

The current execution environment permits Claude Code's model process to use
the pre-approved 8082 proxy but blocks alternate loopback ports before the
connection reaches pproxy's handler. Therefore the modified source cannot be
tested with real `claude -p` on 8084 from this executor. The final attribution
still requires a short restart of 8082 with the modified source; do not claim
that the fragmentation fix alone resolves Claude until that gate passes.

### Update 2026-09-02: current Crewmate succeeds; host 2.1.258 differs

After the user restarted 8082 from the modified source, host Claude Code
2.1.258 still returned `ECONNRESET`. The actual
`crewmate-runner:latest` image is pinned to Claude Code 2.1.170; with the target
OpenRouter environment it completed through a modified 8084 listener and
returned `OK`. An 8085 listener that restored the old `HTTP.guess()` behavior
in memory also returned `OK`, denying the fragmented-prefix defect as the
cause of Crewmate compatibility.

The production-shaped gate then ran the Crewmate runner through the user's
actual restarted 8082 process with:

```text
ANTHROPIC_BASE_URL=https://openrouter.ai/api
ANTHROPIC_AUTH_TOKEN=<dev ai/keys OPENROUTER_API_KEY>
ANTHROPIC_API_KEY=
HTTPS_PROXY=http://host.docker.internal:8082
```

It exited zero with `is_error=false` and result `OK`. Current Crewmate can
therefore use OpenRouter through pproxy while remaining pinned to 2.1.170.
The host-only 2.1.258 reset is a separate client/version/environment issue.

## Repro
With `OPENROUTER_API_KEY` populated from AWS Secrets Manager `ai/keys`, run a
bounded Claude Code request with the key redacted from all diagnostics:

```bash
ANTHROPIC_BASE_URL=https://openrouter.ai/api \
ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY" \
ANTHROPIC_API_KEY= \
HTTPS_PROXY=http://127.0.0.1:8082 \
CLAUDE_CODE_MAX_RETRIES=1 \
claude -p --model haiku --max-turns 1 --no-session-persistence \
  --tools "" --output-format json "Reply with exactly OK"
```

Expected: exit 0 and result `OK`.

Observed: result JSON has `is_error=true` and
`API Error: Connection dropped (ECONNRESET)`.

Control: raw non-streaming and streaming `POST
https://openrouter.ai/api/v1/messages` requests through
`http://127.0.0.1:8082` both return HTTP 200 and text `OK`.

## Test [CONFIRMED]
The raw controls and failing Claude CLI command were observed on 2026-09-02.
An initial pure-HTTP shim printed `Unsupported protocol`, but its log also
contained TCP readiness probes; that output is not yet attributed to Claude
Code and must not drive a code change.

## Proposals
- No.1 Proxy environment precedence routes Claude outside the intended listener [DENIED]
- No.2 HTTP protocol detection rejects a valid fragmented proxy preamble [CONFIRMED]
- No.3 Upstream SOCKS relay resets a valid CONNECT tunnel [DENIED]
- No.4 Crewmate 2.1.170 is compatible; host Claude 2.1.258 differs [CONFIRMED]

## No.1 Proxy environment precedence routes Claude outside the intended listener
### Code Change
None. Capture the first client bytes with only lowercase `https_proxy` set,
then repeat with only uppercase `HTTPS_PROXY`; do not use a readiness connection
that can consume or pollute the capture.

### DENIED
High-frequency socket sampling showed the Claude process opening two TCP
connections to the configured 8082 proxy. Raw Bun fetch and curl controls also
used 8082 successfully. Proxy selection is not the source of the observed
8082 reset. Alternate ports are separately blocked by the executor's network
policy before pproxy accepts them, so they cannot be used for the final Claude
gate here.

## No.2 HTTP protocol detection rejects a valid Claude proxy preamble
### Code Change
`HTTP.guess()` now reads one byte to distinguish HTTP from binary protocols,
then reads the remaining three HTTP prefix bytes exactly. Added
`tests/test_protocol_detection.py` with a delayed fragmented CONNECT prefix
and a three-byte SOCKS5 greeting control.

### CONFIRMED
Before the fix, the fragmented CONNECT test returned `False`; after the fix,
both focused tests pass. The live 8084 integration passed fragmented CONNECT
through TLS to OpenRouter, ordinary HTTP CONNECT, and SOCKS5, all with HTTP
200 through `t-arm1:8084`. This confirms the generic protocol defect and its
fix, but not yet the original Claude-on-8082 outcome; that remains the final
gate after an authorized 8082 restart.

## No.3 Upstream SOCKS relay resets a valid CONNECT tunnel
### Code Change
None.

### DENIED
The modified listener, the old-detector control, and actual 8082 all carried
the Crewmate runner through `socks://t-arm1:8084` to OpenRouter and returned
`OK`. The upstream SOCKS relay is not the current Crewmate blocker.

## No.4 Crewmate 2.1.170 is compatible; host Claude 2.1.258 differs
### Code Change
None. This proposal tests the deployed-client boundary.

### CONFIRMED
`crewmate-runner:latest` contains Claude Code 2.1.170. Its one-turn
`claude -p` request succeeded through both the 8084 candidate and actual 8082
listeners. The same container succeeded through an in-memory old-detector
control. Host Claude Code 2.1.258 continued to reset, so that symptom belongs
to the newer host client or local environment, not current Crewmate's pproxy
path.

## Report
Current Crewmate can use OpenRouter through the existing pproxy topology. The
production-shaped 8082 gate returned `OK` with the dev `ai/keys` OpenRouter
credential and no direct Anthropic key. Keep Crewmate pinned to Claude Code
2.1.170; upgrading to 2.1.258 is a separate compatibility change.

The fragmented HTTP-prefix fix remains as an independently proven robustness
fix. Its red/green test is deterministic, and SOCKS5 plus live HTTP/SOCKS
controls prove it preserves the mixed listener. The old-detector A/B denies
that fix as the reason Crewmate works.
