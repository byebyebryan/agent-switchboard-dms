# Bridge and Desktop Contract v3

## Fleet mode

The retained invocation is exactly:

```text
[EXECUTABLE, "fleet", "--json"]
```

The refresh invocation is exactly:

```text
[EXECUTABLE, "fleet", "--refresh", "--json"]
```

`EXECUTABLE` is one configured token, not a shell command. The bridge uses only
the Python standard library; this means no third-party Python packages, not no
runtime dependencies.

The bridge accepts one Fleet v1 document containing bounded, individually
validated Snapshot v2 documents. It validates host order, stable identities,
reachability/error rules, embedded snapshot ownership and timestamps, and
Snapshot project/repository/checkout/task/session backreferences. It emits
bridge v3 and frontend model v4:

```json
{"bridgeVersion":3,"model":{"modelVersion":4,"sourceSchemaVersion":2,"sourceProtocolVersion":2,"sourceFleetVersion":1},"ok":true}
```

Model v4 merges compatible projects by ProjectId but retains per-host routes.
Task identity is `(HostId, TaskId)` and Inbox identity remains the canonical
host-qualified session key. A remote before first success can appear as a host
without rows. An unavailable remote can retain last-good rows marked offline
or stale. SSH targets and absolute paths are never projected.

Fleet or Snapshot v1 produces `fleet_invalid_protocol`. Unknown safe source
fields are ignored. Sensitive future fields, terminal controls, invalid UTF-8,
non-finite numbers, excessive nesting/count/size, malformed UUIDs, and
inconsistent references fail closed.

## Prepare modes

Every action carries the owning HostId to the configured local core. Exact argv
is:

```text
[EXECUTABLE, "prepare-open", SESSION_KEY,
 "--host", HOST_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]

[EXECUTABLE, "prepare-task", TASK_ID,
 "--host", HOST_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]

[EXECUTABLE, "prepare-task", TASK_ID, "--host", HOST_ID, "--create",
 "--project", PROJECT_ID, "--title", TITLE,
 "--checkout", CHECKOUT_ID, "--provider", PROVIDER,
 "--request-id", UUID, "--can-focus-desktop",
 "--can-launch-terminal", "--json"]

[EXECUTABLE, "prepare-history", "--project", PROJECT_ID,
 "--host", HOST_ID, "--checkout", CHECKOUT_ID,
 "--request-id", UUID, "--can-focus-desktop",
 "--can-launch-terminal", "--json"]
```

Checkout is optional at the bridge/desktop boundary when core can resolve the
host-local default. DMS creation rows include an eligible default checkout.

Successful prepare responses contain one independently validated
PresentationPlan v2:

```json
{"bridgeVersion":3,"ok":true,"plan":{"hostId":"11111111-1111-4111-8111-111111111111","kind":"focus","surfaceId":"33333333-3333-4333-8333-333333333333","desktopToken":"opaque"}}
```

The plan HostId must equal the requested owner. `switchboard-open` generates
request IDs and, for creation, the TaskId. A focus miss repeats the same target
and request with desktop focus disabled, preserving core idempotency.

## Stop, select, and attach

Stop and in-terminal selection use:

```text
[EXECUTABLE, "stop-session", CLAUDE_SESSION_KEY,
 "--host", HOST_ID, "--json"]

[EXECUTABLE, "select-surface", SURFACE_ID,
 "--host", HOST_ID, "--client", TMUX_CLIENT]
```

The launched Ghostty executes only:

```text
[EXECUTABLE, "attach-surface", SURFACE_ID, "--host", HOST_ID]
```

Core decides whether a host-qualified command is local or remote. DMS never
constructs SSH argv and never receives a raw tmux target. Stop consumes one
validated SessionAction v2. Successful desktop execution emits a separate
`actionVersion: 3` envelope with `focused`, `switched`, `launched`, or
`stopped`.

## Project catalog handoff

Project management remains a local core surface. DMS starts the wrapper as:

```text
[SWITCHBOARD_PROJECTS,
 "--swbctl", EXECUTABLE,
 "--terminal", TERMINAL,
 "--timeout-ms", TIMEOUT,
 optional "--project", PROJECT_ID | optional "--add-project"]
```

The two optional targets are mutually exclusive. If the exact niri application
ID `com.agent_switchboard.projects` already exists once, the wrapper focuses it
and waits for it to close. Otherwise it starts fixed argv:

```text
[TERMINAL, "--class=com.agent_switchboard.projects", "-e",
 EXECUTABLE, "tui", "--view", "projects",
 optional "--project", PROJECT_ID | optional "--add-project"]
```

The configured timeout bounds each niri and refresh subprocess; it does not
limit how long a human may use the TUI. After that window closes, the wrapper
invokes its sibling bridge with:

```text
[SWITCHBOARD_BRIDGE, "--swbctl", EXECUTABLE,
 "--timeout-ms", TIMEOUT, "--refresh"]
```

It forwards exactly one Bridge v3 record plus LF and preserves the
bridge success/failure exit convention. Wrapper failures use the same Bridge
v3 error shape. Stderr stays empty. This path invokes no provider, constructs
no SSH command, reads no core-private state, and leaves no background daemon.

## Framing and limits

Source stdout must contain exactly one JSON document with no leading or
trailing JSON whitespace except one optional final LF. Stderr is drained but
never copied into frontend output. The bridge emits one canonical JSON object
plus LF, keeps stderr empty, exits 0 for success, and exits 1 for a structured
failure. Argument errors are argparse exit 2 with empty stdout.

The bridge bounds source bytes, JSON depth, strings, arrays, objects, hosts,
project routes, tasks, Inbox sessions, warnings, and final output.
`--max-sessions` limits Inbox projection; task rows have a separate bound.
Timeout, output overflow, read/selector failure, and unexpected exceptions
kill and reap the entire child process group.

Representative failures are:

| Code | Retryable | Meaning |
| --- | --- | --- |
| `fleet_invalid_utf8` | no | Source output was not UTF-8. |
| `fleet_invalid_json` | no | Framing or JSON syntax was invalid. |
| `fleet_invalid_protocol` | no | The document was not compatible Fleet v1. |
| `plan_invalid_protocol` | no | The document was not PresentationPlan v2. |
| `desktop_plan_host_mismatch` | no | Core returned a plan for another host. |
| `swbctl_nonzero_exit` | yes | Core exited unsuccessfully. |
| `process_timeout` | yes | The configured deadline expired. |
| `stdout_overflow` | no | Source stdout exceeded the byte limit. |
| `bridge_output_overflow` | no | The frontend envelope exceeded its limit. |

The bridge is frontend glue. It does not read SQLite, invoke providers, inspect
transcripts, run Git or SSH, or manage tmux directly.
