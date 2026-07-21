# Switchboard for DMS

Switchboard is the task-first DankMaterialShell launcher for local and
configured remote Agent Switchboard work. Its default view lists open tasks,
merges compatible projects across hosts, keeps unassigned provider sessions in
Inbox, and exposes closed tasks separately. Codex, Claude Code, and
not-yet-started tasks use distinct icons; normal rows show concise project,
optional remote host, optional nondefault worktree, state, and age instead of
absolute paths or SSH targets.

The launcher reads Fleet v1, whose host entries each contain an independently
validated Snapshot v2, into a last-good cache. It persists only the bounded
frontend model in DMS plugin state. Selecting a task invokes `prepare-task`;
selecting an Inbox session invokes `prepare-open`. In a project category, a
nonempty query offers Codex and Claude creation rows for every eligible host.
The desktop helper generates stable task and request UUIDs and calls atomic
new-task preparation, so a focus fallback cannot create a second task or
provider runtime.

The separate Projects category remains available even before a fleet model has
loaded. It shows only projects with a local route, plus Add Project and Manage
Projects actions. Those rows open core's complete project catalog manager in a
singleton Ghostty window; paths and mutation payloads never enter the launcher
model.

Close task is the first secondary action on open task rows and requires no
handoff form or confirmation. It closes first, asks core to stop only a safely
owned runtime, refreshes the Fleet, and reports a cleanup warning without
leaving the task in Open. Selecting a Closed task reopens and opens/resumes it
in one action.

Claude's native history picker and safe launch-owned runtime stop are context
menu actions. They do not create duplicate search rows. History remains inside
Claude's unmodified picker, and stop for either provider remains subject to
core's independent
launch, surface, tmux, PID/birth, UID, and process-group checks.

## Boundary

`switchboard-bridge` runs one user-configured local `swbctl` executable token
without a shell and consumes only public Fleet v1, Snapshot v2,
PresentationPlan v2, SessionAction v2, and TaskCloseAction v2 JSON. It does not
import Agent
Switchboard internals, read its database or remote configuration, invoke Git or
SSH, inspect provider transcripts, or own provider/tmux lifecycle.
`switchboard-open` executes validated host-qualified focus/switch/attach plans
and delegates final tmux attachment back to local `swbctl attach-surface`.
Core alone decides whether an action crosses SSH.
`switchboard-projects` focuses or opens `swbctl tui --view projects`, waits for
that manager window to close, then runs the sibling bridge's full refresh. It
does not edit configuration itself or invoke a provider.

The bridge uses only the Python standard library and no third-party Python
packages. That means dependency-free Python code, not no runtime dependencies.

## Runtime prerequisites

- Python 3.12 or newer.
- An Agent Switchboard 0.2.0 development build exposing Fleet v1 and
  host-qualified actions, installed on `PATH` or configured as one executable
  token. The value is not a shell command and cannot include arguments.
- DMS 1.5.0 or newer and the Quickshell runtime supplied by DMS.
- niri, Ghostty, and a systemd user manager for desktop presentation.

Plugin settings contain the `swbctl` and terminal executable tokens, a
100-60000 ms fleet timeout, and a 5-300 second refresh interval. A retained
read happens first; stale data coalesces one fleet refresh. Parse, validation,
process, and timeout failures keep the last-good fleet visible. Missing
observations, unavailable hosts, and stale data are not converted into
activity guesses.

DMS 1.5.0 does not consume launcher `itemsChanged()` as a live result-list
mutation. A validated persisted model makes normal shell starts and plugin
reloads immediately useful while a new read runs. Only a first install, a
cleared cache, or an invalid cache can expose the initial reading row; if that
row is already open, the new result appears when the launcher is reopened or
the query changes. Dynamic project categories use DMS's native launcher
category contract. Closing the project manager performs a bounded full bridge
refresh and persists its validated result, so the next picker instance sees
catalog changes without requiring a manual refresh keystroke.

Bounded operational status is available without model contents or stable IDs:

```sh
dms ipc call switchboard-launcher status
dms ipc call switchboard-launcher refresh
```

## Direct helper use

Read retained or fully reconciled state:

```sh
./switchboard-bridge
./switchboard-bridge --refresh
```

Open the full project catalog, one selected project, or the add wizard:

```sh
./switchboard-projects
./switchboard-projects --project PROJECT-UUID
./switchboard-projects --add-project
```

The helper uses the configured `swbctl` and terminal executable tokens. It
requires the supported niri/Ghostty desktop path and remains alive only while
the singleton manager window is open.

Open an existing task or exact Inbox session:

```sh
./switchboard-open --host HOST-UUID --window-host HOST --task TASK-UUID
./switchboard-open --host HOST-UUID --window-host HOST \
  HOST-UUID:codex:SESSION-UUID
```

Create and open a task atomically:

```sh
./switchboard-open --host HOST-UUID --window-host HOST --create \
  --project PROJECT-UUID --title "Fix picker layout" \
  --checkout CHECKOUT-UUID --provider codex
```

Open Claude history, close a task, or stop one core-confirmed managed runtime:

```sh
./switchboard-open --host HOST-UUID --window-host HOST --history \
  --project PROJECT-UUID --checkout CHECKOUT-UUID
./switchboard-open --host HOST-UUID --window-host HOST --close-task TASK-UUID
./switchboard-open --host HOST-UUID --window-host HOST \
  --stop HOST-UUID:codex:SESSION-UUID
```

Managed helpers emit one bounded canonical JSON record on stdout and keep
stderr empty. See [docs/bridge-contract.md](docs/bridge-contract.md) for exact
argv, output, and failure rules.

## Development

Run the complete deterministic check lane:

```sh
./scripts/check
```

It covers Fleet v1/model v5 projection, host-qualified task/category/row
behavior, atomic task argv, remote-owner action routing, niri/Ghostty execution,
project-manager focus/launch/refresh behavior, process-group cleanup and fault
injection, static QML contracts, and documentation.

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
[docs/live-integration.md](docs/live-integration.md) before use. The adapter
does not perform a chezmoi cutover, construct SSH commands, write the project
catalog directly, accept an arbitrary working-directory launch, expose a
direct tmux locator, implement non-niri/non-Ghostty adapters, or become a rich
widget. Project mutations occur only inside core's public TUI/CLI contract.

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
