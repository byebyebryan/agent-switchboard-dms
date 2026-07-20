# Switchboard for DMS

Switchboard is the task-first DankMaterialShell launcher for local Agent
Switchboard work. Its default view lists open tasks, groups them by project,
keeps unassigned provider sessions in Inbox, and exposes closed tasks
separately. Codex, Claude Code, and not-yet-started tasks use distinct icons;
normal rows show concise project, optional nondefault worktree, state, and age
instead of absolute paths.

The launcher reads a validated Snapshot v2 asynchronously into an in-memory
last-good cache. Selecting a task invokes `prepare-task`; selecting an Inbox
session invokes `prepare-open`. In a project category, a nonempty query offers
Codex and Claude creation rows using the query as the task title. The desktop
helper generates stable task and request UUIDs and calls atomic new-task
preparation, so a focus fallback cannot create a second task or provider
runtime.

Claude's native history picker and safe launch-owned runtime stop are context
menu actions. They do not create duplicate search rows. History remains inside
Claude's unmodified picker, and stop remains subject to core's independent
launch, surface, tmux, PID/birth, UID, and process-group checks.

## Boundary

`switchboard-bridge` runs one user-configured `swbctl` executable token without
a shell and consumes only public Snapshot v2, PresentationPlan v2, and
SessionAction v2 JSON. It does not import Agent Switchboard internals, read its
database, invoke Git, inspect provider transcripts, or own provider/tmux
lifecycle. `switchboard-open` executes validated focus/switch/attach plans and
delegates final tmux attachment back to `swbctl attach-surface`.

The bridge uses only the Python standard library and no third-party Python
packages. That means dependency-free Python code, not no runtime dependencies.

## Runtime prerequisites

- Python 3.12 or newer.
- Agent Switchboard 0.2.0's `swbctl`, installed on `PATH` or configured as one
  executable token. The value is not a shell command and cannot include
  arguments.
- DMS 1.5.0 or newer and the Quickshell runtime supplied by DMS.
- niri, Ghostty, and a systemd user manager for desktop presentation.

Plugin settings contain the `swbctl` and terminal executable tokens, a
100-60000 ms snapshot timeout, and a 5-300 second refresh interval. A retained
read happens first; stale data coalesces one full reconciliation. Parse,
validation, process, and timeout failures keep the last-good snapshot visible.
Missing observations and stale data are not converted into activity guesses,
and an absent provider capability remains neutral.

DMS 1.5.0 does not consume launcher `itemsChanged()` as a live result-list
mutation. The refreshed cache appears when the launcher is reopened or the
query changes. Dynamic project categories use DMS's native launcher category
contract.

## Direct helper use

Read retained or fully reconciled state:

```sh
./switchboard-bridge
./switchboard-bridge --refresh
```

Open an existing task or exact Inbox session:

```sh
./switchboard-open --window-host HOST --task TASK-UUID
./switchboard-open --window-host HOST HOST-ID:codex:SESSION-UUID
```

Create and open a task atomically:

```sh
./switchboard-open --window-host HOST --create \
  --project PROJECT-UUID --title "Fix picker layout" \
  --checkout CHECKOUT-UUID --provider codex
```

Open Claude history or stop one core-confirmed Claude runtime:

```sh
./switchboard-open --window-host HOST --history \
  --project PROJECT-UUID --checkout CHECKOUT-UUID
./switchboard-open --window-host HOST --stop HOST-ID:claude:SESSION-UUID
```

Managed helpers emit one bounded canonical JSON record on stdout and keep
stderr empty. See [docs/bridge-contract.md](docs/bridge-contract.md) for exact
argv, output, and failure rules.

## Development

Run the complete deterministic check lane:

```sh
./scripts/check
```

It covers Snapshot v2/model v3 projection, task categories and row formatting,
atomic task argv, context-action routing, niri/Ghostty execution, process-group
cleanup and fault injection, static QML contracts, and documentation.

Install this checkout as the local development plugin:

```sh
./scripts/dev-plugin install
dms ipc call plugin-scan scan
dms ipc call plugin-scan status switchboard
dms ipc call plugins enable switchboard
dms ipc call plugins reload switchboard
```

`dev-plugin` creates only a `switchboard` symlink in the DMS user plugin
directory and refuses foreign or unsafe destinations. Remove only this
checkout's link with:

```sh
dms ipc call plugins disable switchboard
./scripts/dev-plugin remove
dms ipc call plugin-scan scan
```

The component harness uses private Agent Switchboard state and does not touch a
live provider session:

```sh
./scripts/live-integration --swbctl /path/to/swbctl
```

The separate disruptive shell verifier restarts DMS and must be requested
explicitly:

```sh
./scripts/live-shell-integration --swbctl /path/to/swbctl --confirm-disruptive
```

It installs rollback traps before mutation; see
[docs/live-integration.md](docs/live-integration.md) before use. The repository
does not perform a chezmoi cutover, add SSH, edit the project catalog, accept an
arbitrary working-directory launch, expose a direct tmux locator, implement
non-niri/non-Ghostty adapters, or become a rich widget.

For local diagnostics, also run:

```sh
/usr/lib/qt6/bin/qmlformat SwitchboardLauncher.qml SwitchboardSettings.qml tests/live/Shell.qml
/usr/lib/qt6/bin/qmllint -I /usr/share/quickshell/dms SwitchboardLauncher.qml SwitchboardSettings.qml tests/live/Shell.qml
ruff check .
ruff format --check .
pyright switchboard_dms
```

Standalone `qmllint` cannot resolve every DMS-specific `qs.*` import, so it is
diagnostic rather than a warning-free gate.
