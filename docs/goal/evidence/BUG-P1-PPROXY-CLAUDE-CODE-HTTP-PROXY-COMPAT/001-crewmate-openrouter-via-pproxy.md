# BUG-P1-PPROXY-CLAUDE-CODE-HTTP-PROXY-COMPAT — Crewmate OpenRouter proof

## Source identity

- Repository HEAD: `9dbb1404328e37c5318aeef1d592cb5fa2c1eb40`
- Worktree: dirty; this receipt covers only the paths named below.
- Changed paths: `projects/python-proxy/pproxy/proto.py` and
  `projects/python-proxy/tests/test_protocol_detection.py`.
- Runtime image: `crewmate-runner:latest` (`853250640bb1`), Claude Code
  `2.1.170`.

## Changed behavior

`HTTP.guess()` no longer assumes one TCP read returns a complete four-byte
method prefix. It reads one discriminator byte, rolls binary protocols back
immediately, and reads the remaining three bytes exactly only for a possible
HTTP method. This preserves the three-byte SOCKS5 greeting handshake.

## Evidence

Focused regression:

```text
cd projects/python-proxy
gtimeout 120s env -u LC_ALL uv run python -m pytest -q -x -n0 tests
2 passed in 0.03s
```

Live modified-listener integration on 8084, upstream
`socks://t-arm1:8084`:

```text
fragmented_connect_tls_status=HTTP/1.1 200 OK
http_proxy_status=200
socks5_status=200
```

Crewmate container through modified 8084:

```text
crewmate_container_claude_via_8084_exit=0
is_error=false
result=OK
```

Same container through an in-memory old `HTTP.guess()` control on 8085:

```text
old_detector_control_exit=0
is_error=false
result=OK
```

Final production-shaped gate through the user's restarted actual 8082 proxy:

```text
crewmate_container_claude_via_actual_8082_exit=0
is_error=false
result=OK
```

The key came from AWS Secrets Manager `ai/keys` and was passed only through
environment variables. No credential value was printed or persisted in this
receipt.

## Supported claim

The current Crewmate runner can execute `claude -p` through pproxy and
OpenRouter using `ANTHROPIC_BASE_URL=https://openrouter.ai/api`, the OpenRouter
key mapped to `ANTHROPIC_AUTH_TOKEN`, and an empty `ANTHROPIC_API_KEY`.

## Not proven

This does not repair or explain the host-installed Claude Code 2.1.258
`ECONNRESET`; that client continued to fail even after 8082 restarted. The
old-detector A/B proves the pproxy fragmentation fix is not the cause of the
Crewmate result.
