# Switchboard for DMS

Switchboard is a future DankMaterialShell (DMS) launcher plugin for browsing
Agent Switchboard snapshots. This repository currently contains only an inert,
loadable scaffold: it returns no launcher items and executes no actions.

The integration boundary is deliberately narrow. A future implementation will
run a user-configured `swbctl` executable and consume Snapshot v1 JSON through
the public commands documented in [docs/architecture.md](docs/architecture.md).
It will not import Agent Switchboard internals or read its database directly.

## Development

Run the dependency-free baseline checks with:

```sh
./scripts/check
```

The checks validate the plugin manifest, QML scaffold surface, architecture
contract, and the pinned synthetic protocol fixture. QML runtime tests and
linting are intentionally not claimed at this stage.

See [docs/implementation-plan.md](docs/implementation-plan.md) for the phased
path from this scaffold to a read-only launcher integration.
