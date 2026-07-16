# Bridge contract

`switchboard-bridge` is the dependency-free subprocess boundary between DMS
and Agent Switchboard. It invokes only the public `swbctl snapshot` command,
validates Snapshot v1, and emits a smaller frontend-owned model. The bridge
itself does not directly read Switchboard storage or invoke providers,
transports, terminals, or the compositor. `--refresh` intentionally asks
`swbctl` to perform full reconciliation behind that public boundary.

## Invocation

```sh
./switchboard-bridge [--swbctl EXECUTABLE] [--refresh]
    [--timeout-ms MILLISECONDS] [--max-sessions COUNT]
```

- `--swbctl` defaults to the single executable token `swbctl`. The value is
  passed directly as one argv element; it is never shell-split or interpreted.
- A retained read uses exactly `[EXECUTABLE, "snapshot", "--json"]`.
- `--refresh` uses exactly `[EXECUTABLE, "snapshot", "--reconcile", "full",
  "--json"]`.
- `--timeout-ms` defaults to `10000` and accepts `100` through `60000`.
- `--max-sessions` defaults to `1000` and accepts `1` through `1000`.

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
{"bridgeVersion":1,"model":{"modelVersion":1},"ok":true}
```

`model` is the complete output of the bounded Snapshot v1 projection described
in `switchboard_dms.protocol`. Provider degradation is data: a valid degraded
or neutral snapshot remains `ok: true`, with capability state and warnings in
the model.

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
| `executable_start_failed` | yes | The operating system could not start it. |
| `process_timeout` | yes | The bounded deadline expired. |
| `stdout_overflow` | no | Snapshot stdout exceeded its limit. |
| `stderr_overflow` | no | Diagnostic stderr exceeded its limit. |
| `swbctl_nonzero_exit` | yes | `swbctl` returned a nonzero status. |
| `snapshot_invalid_utf8` | no | Snapshot stdout was not UTF-8. |
| `snapshot_invalid_json` | no | Snapshot stdout was not one valid JSON document. |
| `snapshot_invalid_protocol` | no | JSON was not a compatible Snapshot v1 envelope. |
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
- No shell, `shlex`, private database, provider command, tmux, SSH, niri, or
  Ghostty integration exists in this boundary.

These limits are consumed by the asynchronous QML process adapter. The launcher
passes the configured executable and timeout as separate argv elements,
requests `--refresh` only for full reconciliation, and replaces its cache only
after a complete exit-zero success envelope passes frontend model validation.

## Reviewed contract provenance

This consumer was reviewed against the public Snapshot v1 contract in
`byebyebryan/agent-switchboard` at commits
`898fa1080712235993781c27c56d312e8e3cef9e` and
`b3b54b4dc1eea5a5b0bd78792fa6c7f626701a8f`. The pinned synthetic fixture is
copied from `tests/fixtures/protocol/v1/snapshot.json` at those revisions and
has SHA-256
`fd3146e6f62eff8fe607227a7b22453f3ffbdcc1de28754da23ecc8c72dd10cb`.
Runtime code does not import the core repository.
