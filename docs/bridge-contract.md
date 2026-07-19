# Bridge contract

`switchboard-bridge` is the subprocess boundary between DMS and Agent
Switchboard. Its Python implementation uses only the Python standard library;
"dependency-free" means no third-party Python packages, not no runtime
dependencies. The Python, Agent Switchboard/`swbctl`, DMS, and DMS-supplied
Quickshell prerequisites are listed in the README.

The bridge invokes only public `swbctl` commands. Snapshot reads validate
Snapshot v1 and emit a smaller frontend-owned model. Action modes validate
PresentationPlan v1 or SessionAction v1, or delegate one exact surface
selection. It does not
directly read Switchboard storage or invoke providers, terminals, or the
compositor. `--refresh`, `prepare-open`, `prepare-new`, `prepare-history`, and
`stop-session` intentionally ask `swbctl` to perform full reconciliation behind
that public boundary.

## Invocation

```sh
./switchboard-bridge [--swbctl EXECUTABLE] [--refresh]
    [--timeout-ms MILLISECONDS] [--max-sessions COUNT]
./switchboard-bridge [--swbctl EXECUTABLE]
    --prepare-open SESSION-KEY --request-id UUID
    [--timeout-ms MILLISECONDS]
./switchboard-bridge [--swbctl EXECUTABLE]
    --prepare-new PROJECT-ID --location LOCATION-ID --provider codex|claude
    --request-id UUID
    [--timeout-ms MILLISECONDS]
./switchboard-bridge [--swbctl EXECUTABLE]
    --prepare-history PROJECT-ID --location LOCATION-ID --request-id UUID
    [--timeout-ms MILLISECONDS]
./switchboard-bridge [--swbctl EXECUTABLE]
    --stop-session SESSION-KEY [--timeout-ms MILLISECONDS]
./switchboard-bridge [--swbctl EXECUTABLE]
    --select-surface SURFACE-ID --tmux-client CLIENT-ID
    [--timeout-ms MILLISECONDS]
```

- `--swbctl` defaults to the single executable token `swbctl`. The value is
  passed directly as one argv element; it is never shell-split or interpreted.
- A retained read uses exactly `[EXECUTABLE, "snapshot", "--json"]`.
- `--refresh` uses exactly `[EXECUTABLE, "snapshot", "--reconcile", "full",
  "--json"]`.
- `--timeout-ms` defaults to `10000` and accepts `100` through `60000`.
- `--max-sessions` defaults to `1000` and accepts `1` through `1000`.
- `--prepare-open`, `--prepare-new`, `--prepare-history`, `--stop-session`, and
  `--select-surface` are mutually exclusive with one another and with
  `--refresh`.
- Existing-session preparation uses exactly `[EXECUTABLE, "prepare-open", SESSION_KEY,
  "--request-id", UUID, "--can-focus-desktop", "--can-launch-terminal",
  "--json"]`.
- New-session preparation uses exactly `[EXECUTABLE, "prepare-new",
  "--project", PROJECT_ID, "--location", LOCATION_ID, "--provider", PROVIDER,
  "--request-id", UUID, "--can-focus-desktop", "--can-launch-terminal",
  "--json"]`.
- Native-history preparation uses exactly `[EXECUTABLE, "prepare-history",
  "--project", PROJECT_ID, "--location", LOCATION_ID, "--request-id", UUID,
  "--can-focus-desktop", "--can-launch-terminal", "--json"]`.
- Stop uses exactly `[EXECUTABLE, "stop-session", SESSION_KEY, "--json"]` and
  accepts only canonical local Claude session keys.
- Selection uses exactly `[EXECUTABLE, "select-surface", SURFACE_ID,
  "--client", CLIENT_ID]` and requires empty stdout on success.

The root entry point resolves its real path so it can be launched through a
development symlink, but it must remain co-located with the `switchboard_dms`
package in an installed or checked-out plugin directory.

Invalid arguments use normal argparse behavior and may exit `2` with usage on
stderr. After argument parsing succeeds, every managed run writes nothing to
stderr and, while stdout remains writable, writes exactly one compact,
key-sorted, newline-terminated JSON object there. A stdout write or flush
failure exits `1` without a traceback or diagnostic text; the destination may
contain no JSON or a partial record because the output channel failed.

## Success

Success exits `0`:

```json
{"bridgeVersion":1,"model":{"modelVersion":2},"ok":true}
```

`model` is the complete output of the bounded Snapshot v1 projection described
in `switchboard_dms.protocol`. Model v2 projects local Codex and Claude session
rows, an ordered capability record for each provider, and provider-attributed
warnings. Every declared local tmux location produces distinct Codex and Claude
launch targets. Provider degradation is data: a valid degraded or neutral
snapshot remains `ok: true`, with capability state and warnings in the model.

A prepared action returns the independently validated public plan:

```json
{"bridgeVersion":1,"ok":true,"plan":{"hostId":"11111111-1111-4111-8111-111111111111","kind":"focus","surfaceId":"33333333-3333-4333-8333-333333333333","desktopToken":"opaque"}}
```

A successful selection returns only the selected stable surface ID:

```json
{"action":{"kind":"selected","surfaceId":"33333333-3333-4333-8333-333333333333"},"bridgeVersion":1,"ok":true}
```

A stop returns only the independently validated public action:

```json
{"action":{"hostId":"11111111-1111-4111-8111-111111111111","kind":"stop","sessionKey":"11111111-1111-4111-8111-111111111111:claude:55555555-5555-4555-8555-555555555555","status":"stopped"},"bridgeVersion":1,"ok":true}
```

`already_stopped` is also successful. The bridge preserves a validated core
`blocked` action; `switchboard-open` converts it to the bounded structured
failure consumed by QML and never treats it as a successful UI action.

## Failure

Managed failure exits `1`:

```json
{"bridgeVersion":1,"error":{"code":"process_timeout","message":"swbctl did not finish before the configured timeout.","retryable":true},"ok":false}
```

The stable error codes are:

| Code | Retryable | Meaning |
| --- | --- | --- |
| `executable_not_found` | no | The configured executable was not found. |
| `executable_permission_denied` | no | The configured file is not executable. |
| `process_timeout` | yes | The bounded deadline expired. |
| `stdout_overflow` | no | Snapshot stdout exceeded its limit. |
| `stderr_overflow` | no | Diagnostic stderr exceeded its limit. |
| `swbctl_nonzero_exit` | yes | `swbctl` returned a nonzero status. |
| `snapshot_invalid_utf8` | no | Snapshot stdout was not UTF-8. |
| `snapshot_invalid_json` | no | Snapshot stdout was not one valid JSON document. |
| `snapshot_invalid_protocol` | no | JSON was not a compatible Snapshot v1 envelope. |
| `plan_invalid_protocol` | no | JSON was not a compatible PresentationPlan v1 envelope. |
| `action_invalid_protocol` | no | JSON was not a compatible SessionAction v1 envelope. |
| `action_unexpected_output` | no | A successful selection wrote unexpected stdout. |
| `bridge_serialization_failed` | no | The bridge model could not be serialized. |
| `bridge_output_overflow` | no | The final bridge response exceeded its limit. |
| `bridge_internal_error` | no | An otherwise unmanaged bridge error occurred. |

Messages are short display-safe summaries. The bridge does not expose or parse
stderr prose, provider output, paths, or exception details to select an error
code.

## Resource and privacy bounds

- stdout and stderr are drained concurrently while the child runs; the bridge
  never uses an unbounded `communicate()` call.
- If both streams cross their limits concurrently, the first readiness event
  observed determines the reported overflow code; both buffers remain bounded
  and the same process-group cleanup is applied.
- Snapshot JSON is limited to 8 MiB. One final LF byte is permitted outside
  that JSON limit. Leading or trailing JSON whitespace, including spaces,
  tabs, CRLF, and multiple final newlines, is rejected so framing is exactly
  one JSON document with zero or one final LF.
- stderr is limited to 64 KiB and is never forwarded. Protocol capability,
  degradation, error, and warning collections are independently count- and
  byte-bounded before they enter the frontend model.
- Final bridge stdout, including its required newline, is limited to 8 MiB.
- `swbctl` starts in a new process group. Every abnormal execution or cleanup
  exit kills the group and reaps the direct child, including cleanup faults
  discovered after the child itself exited normally.
- No shell, `shlex`, private database, provider command, SSH, niri, or Ghostty
  integration exists in this boundary. tmux selection remains behind the
  revalidating public `swbctl select-surface` command.

These limits are consumed by the asynchronous QML process adapter. The launcher
passes the configured executable and timeout as separate argv elements,
requests `--refresh` only for full reconciliation, and replaces its cache only
after a complete exit-zero success envelope passes frontend model validation.

## Desktop action envelope

`switchboard-open` accepts one session key, one project/location/provider
target, one project/location Claude-history target, or one stop session key and
emits a separate `actionVersion: 1` envelope. Presentation success kinds are
`focused`, `switched`, or `launched`, each with one stable surface ID; stop
success is `stopped` with core-authored `stopped` or `already_stopped` status.
Failure uses the same bounded `{code,message,retryable}` display shape. The
helper's stdout is one compact newline-terminated JSON object no larger than 16
KiB; stderr is ignored by QML. It accepts one `--swbctl` token, one `--terminal`
token, a bounded `--window-host`, the shared timeout, and either one canonical
Codex or Claude session key, canonical project and location IDs plus the
bounded provider enum, canonical project/location history IDs, or one canonical
Claude stop key. It never accepts cwd, raw tmux locators, provider argv, desktop
tokens, or niri window IDs from QML.

## Reviewed contract provenance

This consumer was reviewed against the public Snapshot v1 contract in
`byebyebryan/agent-switchboard` at commits
`898fa1080712235993781c27c56d312e8e3cef9e` and
`b3b54b4dc1eea5a5b0bd78792fa6c7f626701a8f`. The pinned synthetic fixture is
copied from `tests/fixtures/protocol/v1/snapshot.json` at those revisions and
has SHA-256
`fd3146e6f62eff8fe607227a7b22453f3ffbdcc1de28754da23ecc8c72dd10cb`.
Runtime code does not import the core repository.
