# Switchboard for DMS

Switchboard is a DankMaterialShell (DMS) launcher integration for browsing
local Codex sessions from Agent Switchboard snapshots. The QML launcher returns
filtered rows from an in-memory last-good cache while a persistent DMS
`Process` runs the repository-owned bridge asynchronously. Session selection
remains intentionally unavailable; `executeItem(item)` is a safe no-op until a
separate public action contract exists.

The integration boundary is deliberately narrow. `switchboard-bridge` runs a
user-configured `swbctl` executable without a shell and consumes Snapshot v1
JSON through the public commands documented in
[docs/architecture.md](docs/architecture.md). It does not import Agent
Switchboard internals, read its database directly, or invoke provider commands
itself. `--refresh` intentionally asks the public `swbctl` boundary to perform
full reconciliation before returning the snapshot.

Plugin settings expose only the integration controls needed by this phase:

- `swbctl` executable token, defaulting to normal lookup of `swbctl` and
  limited to 4096 JavaScript UTF-16 code units
- snapshot timeout from 100 through 60000 milliseconds, defaulting to 10000
- refresh interval from 5 through 300 seconds, defaulting to 15

The first background run is a retained read. A snapshot older than the refresh
interval coalesces one full refresh. A complete successful bridge response
atomically replaces the cache; command, timeout, parse, and validation failures
leave the last-good session rows in place and add an explicit failure item.
Provider degradation and neutral capability state remain distinct from process
failure. Session activity and runtime labels are copied from the source model,
never inferred from missing or stale observations.

DMS 1.5.0 does not consume launcher `itemsChanged()`. Background completion
therefore does not mutate an already rendered result list; the refreshed cache
appears when the launcher is reopened or its query changes.

Run a retained read with normal executable lookup:

```sh
./switchboard-bridge
```

Request a full refresh with:

```sh
./switchboard-bridge --refresh
```

Managed bridge runs keep stderr empty and, while stdout remains writable, emit
exactly one JSON object. They use exit `0` for valid models or exit `1` for
structured failures; a broken stdout also exits `1` without diagnostic output.
See
[docs/bridge-contract.md](docs/bridge-contract.md) for the complete argv,
limit, output, and error contract.

## Development

Run the dependency-free baseline checks with:

```sh
./scripts/check
```

The checks validate the plugin manifest, static QML cache/process surface,
deterministic JavaScript projection and search behavior, architecture and
bridge contracts, bounded process behavior, Snapshot projection, and the pinned
synthetic protocol fixture. QML runtime tests are intentionally not claimed
in CI because the live harness needs installed DMS imports and an active
display.

Install this checkout as the local development plugin with:

```sh
./scripts/dev-plugin install
dms ipc call plugin-scan scan
dms ipc call plugin-scan status switchboard
dms ipc call plugins enable switchboard
dms ipc call plugins reload switchboard
```

`dev-plugin` derives the DMS user plugin directory from `XDG_CONFIG_HOME` and
creates only a `switchboard` symlink back to the current checkout. It refuses
foreign destinations, symlinked plugin directories, and paths not owned by the
current user. Use `--plugin-dir` when DMS is configured elsewhere. The
registry-only `dms plugins install` command does not accept a local checkout.

Disable and remove only this checkout's link with:

```sh
dms ipc call plugins disable switchboard
./scripts/dev-plugin remove
dms ipc call plugin-scan scan
```

The disruptive real-shell lifecycle verifier is deliberately separate:

```sh
./scripts/live-shell-integration --swbctl /path/to/swbctl --confirm-disruptive
```

It restarts DMS and performs real full reconciliation, but installs rollback
traps before mutation and verifies exact restoration. See the runbook before
using it.

Configure the `swbctl` executable through the plugin's DMS settings before
testing data. The repository and scripts never hardcode an Agent Switchboard
checkout. For a component-level runtime exercise using the installed DMS QML
imports and an explicit executable, run:

```sh
./scripts/live-integration --swbctl /path/to/swbctl
```

The harness copies the selected Agent Switchboard state into a private
temporary `XDG_STATE_HOME` and prints summary fields only. It covers settings
focus/height, retained and full-refresh bridge runs, exact internal query
projection, exact last-good retention, configured-executable failure/recovery,
and QML process start failure/recovery. Its process group and temporary state
are removed on normal exit and signals. DMS 1.5.0 can open a launcher query by
IPC but cannot return rendered launcher results by IPC, so shell discovery
evidence remains separate. See
[docs/live-integration.md](docs/live-integration.md) for the sanitized
reproducible runbook, selected evidence, and limitations.

On a DMS 1.5.0 development machine, also run:

```sh
/usr/lib/qt6/bin/qmlformat SwitchboardLauncher.qml SwitchboardSettings.qml tests/live/Shell.qml
/usr/lib/qt6/bin/qmllint -I /usr/share/quickshell/dms SwitchboardLauncher.qml SwitchboardSettings.qml tests/live/Shell.qml
ruff check .
ruff format --check .
pyright switchboard_dms
```

See [docs/implementation-plan.md](docs/implementation-plan.md) for the phased
implementation and [docs/live-integration.md](docs/live-integration.md) for
live DMS evidence.

When Qt 6 `qmlformat` is installed at `/usr/lib/qt6/bin/qmlformat`,
`scripts/check` also byte-compares its output for all three QML files. Qt 6
`qmllint` remains a diagnostic command: DMS's Quickshell-specific `qs.*`
imports are not fully resolved by standalone `qmllint`, so a warning-free lint
gate is not claimed.
