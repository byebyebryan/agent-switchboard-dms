# Bridge and Desktop Contract v2

## Snapshot mode

The retained invocation is exactly:

```text
[EXECUTABLE, "snapshot", "--json"]
```

The refresh invocation is exactly:

```text
[EXECUTABLE, "snapshot", "--reconcile", "full", "--json"]
```

`EXECUTABLE` is one configured token, not a shell command. The bridge uses only
the Python standard library; this means no third-party Python packages, not no
runtime dependencies.

The bridge accepts one Snapshot v2 JSON document, validates the required
projects, projectRepositories, repositories, checkouts, tasks, sessions,
runtimes, surfaces, capabilities, and errors arrays, checks their identity
backreferences, and emits model v3:

```json
{"bridgeVersion":2,"model":{"modelVersion":3,"sourceSchemaVersion":2,"sourceProtocolVersion":2},"ok":true}
```

Snapshot v1 produces `snapshot_invalid_protocol`. Unknown safe source fields
are ignored. Sensitive future fields, terminal controls, invalid UTF-8,
non-finite numbers, excessive nesting/count/size, malformed UUIDs, and
inconsistent references fail closed. Absolute checkout paths and private Git
administrative data are never projected.

## Prepare modes

Existing Inbox session:

```text
[EXECUTABLE, "prepare-open", SESSION_KEY, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]
```

Existing task:

```text
[EXECUTABLE, "prepare-task", TASK_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]
```

Atomic task create:

```text
[EXECUTABLE, "prepare-task", TASK_ID, "--create",
 "--project", PROJECT_ID, "--title", TITLE,
 "--checkout", CHECKOUT_ID, "--provider", PROVIDER,
 "--request-id", UUID, "--can-focus-desktop",
 "--can-launch-terminal", "--json"]
```

Claude history:

```text
[EXECUTABLE, "prepare-history", "--project", PROJECT_ID,
 "--checkout", CHECKOUT_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]
```

The checkout argument is optional at the bridge/desktop boundary when core can
route the project's default. DMS creation rows include the validated default
checkout explicitly.

Successful prepare responses contain one validated PresentationPlan v2:

```json
{"bridgeVersion":2,"ok":true,"plan":{"hostId":"11111111-1111-4111-8111-111111111111","kind":"focus","surfaceId":"33333333-3333-4333-8333-333333333333","desktopToken":"opaque"}}
```

`switchboard-open` generates request IDs. For new-task selection it also
generates the TaskId. A focus miss repeats the identical prepare request with
desktop focus disabled, so core's reservation remains idempotent.

## Stop and surface selection

Stop uses exactly:

```text
[EXECUTABLE, "stop-session", CLAUDE_SESSION_KEY, "--json"]
```

and emits:

```json
{"action":{"hostId":"11111111-1111-4111-8111-111111111111","kind":"stop","sessionKey":"11111111-1111-4111-8111-111111111111:claude:55555555-5555-4555-8555-555555555555","status":"stopped"},"bridgeVersion":2,"ok":true}
```

An in-terminal switch selects a validated surface with:

```text
[EXECUTABLE, "select-surface", SURFACE_ID, "--client", TMUX_CLIENT]
```

Successful desktop execution emits a separate `actionVersion: 2` envelope with
`focused`, `switched`, `launched`, or `stopped`. QML never receives a raw tmux
locator.

## Framing and limits

Source stdout must contain exactly one JSON document with no leading or
trailing JSON whitespace except one optional final LF. Stderr is drained but
never copied into frontend output. The bridge emits one canonical JSON object
plus LF, keeps stderr empty, exits 0 for success, and exits 1 for a structured
failure. Argument errors are argparse exit 2 with empty stdout.

The bridge bounds source bytes, JSON depth, strings, arrays, objects, projected
tasks, Inbox sessions, warnings, and final output. `--max-sessions` limits only
Inbox projection; task rows have their own structural bound. Timeout, output
overflow, read/selector failure, and unexpected exceptions kill and reap the
entire child process group.

Representative failure codes:

| Code | Retryable | Meaning |
| --- | --- | --- |
| `snapshot_invalid_utf8` | no | Source output was not UTF-8. |
| `snapshot_invalid_json` | no | Framing or JSON syntax was invalid. |
| `snapshot_invalid_protocol` | no | The document was not Snapshot v2. |
| `plan_invalid_protocol` | no | The document was not PresentationPlan v2. |
| `swbctl_nonzero_exit` | yes | Core exited unsuccessfully. |
| `process_timeout` | yes | The configured deadline expired. |
| `stdout_overflow` | no | Source stdout exceeded the byte limit. |
| `bridge_output_overflow` | no | The final private envelope exceeded its limit. |

The bridge is frontend glue, not a provider adapter. It does not read SQLite,
invoke Codex or Claude, inspect transcripts, run Git, execute SSH, or manage
tmux directly.
