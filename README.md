# Switchboard for DMS

Switchboard is a DankMaterialShell (DMS) launcher integration for browsing
Agent Switchboard snapshots. The QML launcher is still an inert, loadable
scaffold: it returns no launcher items and executes no actions. The repository
now includes a bounded, dependency-free Snapshot v1 model and subprocess bridge
for the next QML integration phase.

The integration boundary is deliberately narrow. `switchboard-bridge` runs a
user-configured `swbctl` executable without a shell and consumes Snapshot v1
JSON through the public commands documented in
[docs/architecture.md](docs/architecture.md). It does not import Agent
Switchboard internals, read its database directly, or invoke provider commands
itself. `--refresh` intentionally asks the public `swbctl` boundary to perform
full reconciliation before returning the snapshot.

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

The checks validate the plugin manifest, QML scaffold surface, architecture and
bridge contracts, bounded process behavior, Snapshot projection, and the pinned
synthetic protocol fixture. QML runtime tests and linting are intentionally not
claimed in CI at this stage.

See [docs/implementation-plan.md](docs/implementation-plan.md) for the phased
path from this scaffold to a read-only launcher integration.
