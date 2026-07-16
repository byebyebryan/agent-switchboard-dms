# Architecture

## Integration boundary

Switchboard for DMS is a frontend adapter, not a second implementation of
Agent Switchboard. Its entire data boundary is a user-configured `swbctl`
executable plus the public Snapshot v1 JSON envelope. The executable path must
come from plugin settings or normal executable lookup; the plugin must never
hardcode a machine-local path.

The only accepted CLI commands are:

- `swbctl snapshot --json`
- `swbctl snapshot --reconcile full --json`
- `swbctl list --json`
- `swbctl list --refresh --json`

This initial scaffold invokes none of them. Future read-only work may select
among those commands, but it must not import internal Agent Switchboard
modules, query internal databases, or depend on private storage layouts.
Snapshot v1 JSON is the only interchange format at this boundary. Unknown
fields in that envelope are ignored for forward compatibility.

## Launcher data flow

DMS calls `getItems(query)` synchronously, so the future launcher must answer
from an in-memory cache. Refreshing `swbctl` will be asynchronous and separate
from reads:

1. Return filtered items from the current cache immediately.
2. Start or coalesce an asynchronous refresh when the cache is stale.
3. Parse and validate a complete Snapshot v1 response before replacing cache
   state.
4. Retain the last-good snapshot after command, parse, or validation failures.

State presentation must remain honest. Missing observations and stale data are
unknown, not offline, dead, idle, or otherwise inferred facts. An empty
`capabilities` array is neutral when reading a retained last-good snapshot: it
does not retroactively prove that a provider is unavailable. The scaffold has
no selection behavior; selection is unavailable until an explicit, safe
action contract is designed and tested.

The launcher surface exposes `itemsChanged()`, and a future implementation may
also encounter DMS's `requestLauncherUpdate` convention. Current DMS launcher
consumers do not consume either notification for this plugin shape, so a
background refresh becomes visible only when the launcher is reopened or the
query changes. The cache design must not promise live in-place updates.

## Non-goals

This repository does not currently provide:

- Claude support
- SSH support or remote-host orchestration
- provider hooks or liveness inference
- project actions
- tmux session creation
- niri integration
- Ghostty integration
- a chezmoi cutover or configuration migration
- a rich widget

It also does not recreate provider, tmux, SSH, compositor, terminal, or config
management logic inside QML.
