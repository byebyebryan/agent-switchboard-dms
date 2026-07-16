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

The implemented bridge deliberately chooses the two snapshot forms from that
public set: retained reads use `swbctl snapshot --json`, and refreshes use
`swbctl snapshot --reconcile full --json`. It must not import internal Agent
Switchboard modules, query internal databases, or depend on private storage
layouts, and it does not invoke provider commands itself. The refresh form
intentionally asks `swbctl` to reconcile providers behind that public boundary.
Snapshot v1 JSON is the only interchange format at this boundary.
Unknown fields in that envelope are ignored for forward compatibility.

## Process bridge

The repository-owned `switchboard-bridge` executable is the only process
adapter intended for QML. It accepts one configured executable token, builds a
fixed argv array, and never invokes a shell. Its process layer drains stdout and
stderr concurrently under strict byte and time limits. Every abnormal process
or cleanup exit kills the isolated child process group and reaps its direct
child.

The bridge validates UTF-8 and JSON syntax before applying the frontend-owned
Snapshot v1 protocol model. This classification uses exception types and
validation stages, never diagnostic prose. Valid provider degradation remains
a successful model; process and protocol failures become stable structured
errors. Managed runs emit one deterministic JSON object on writable stdout and
nothing on stderr. A broken stdout exits as a silent managed failure. The full
versioned contract is in
[bridge-contract.md](bridge-contract.md).

## Launcher data flow

DMS calls `getItems(query)` synchronously, so the future launcher must answer
from an in-memory cache. Invoking `switchboard-bridge` will be asynchronous and
separate from reads:

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
