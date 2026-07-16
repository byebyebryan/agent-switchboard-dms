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
until the live DMS integration phase.

On a DMS 1.5.0 development machine, also run:

```sh
qmllint -I /usr/share/quickshell/dms SwitchboardLauncher.qml SwitchboardSettings.qml
qmlformat SwitchboardLauncher.qml SwitchboardSettings.qml
ruff check .
ruff format --check .
pyright switchboard_dms
```

See [docs/implementation-plan.md](docs/implementation-plan.md) for the phased
path from this read-only launcher integration to live DMS verification.
