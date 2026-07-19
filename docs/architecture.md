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
- `swbctl prepare-open <session-key> --request-id <uuid> --json`
- `swbctl prepare-new --project <project-id> --location <location-id> --provider <provider> --request-id <uuid> --json`
- `swbctl prepare-history --project <project-id> --location <location-id> --request-id <uuid> --json`
- `swbctl stop-session <session-key> --json`
- `swbctl select-surface <surface-id> --client <tmux-client-id>`
- `swbctl attach-surface <surface-id>`

The implemented bridge deliberately chooses the two snapshot forms from that
public set: retained reads use `swbctl snapshot --json`, and refreshes use
`swbctl snapshot --reconcile full --json`. It must not import internal Agent
Switchboard modules, query internal databases, or depend on private storage
layouts, and it does not invoke provider commands itself. The refresh form
intentionally asks `swbctl` to reconcile providers behind that public boundary.
Snapshot v1 JSON, PresentationPlan v1 JSON, and SessionAction v1 JSON are the only
interchange formats at this boundary. Unknown safe snapshot fields are ignored
for forward compatibility; presentation plans and stop actions are
independently validated before any desktop action.

## Process bridge

The repository-owned `switchboard-bridge` executable is the only process
adapter used for reads and direct core actions. It accepts one configured
executable token, builds fixed argv arrays, and never invokes a shell. Its
process layer drains stdout and stderr concurrently under strict byte and time
limits. Every abnormal process or cleanup exit kills the isolated child process
group and reaps its direct child.

The settings and launcher both bound that opaque executable token to 4096
JavaScript UTF-16 code units. No path syntax or shell syntax is interpreted.

The bridge validates UTF-8 and JSON syntax before applying the frontend-owned
Snapshot v1 protocol model. This classification uses exception types and
validation stages, never diagnostic prose. Valid provider degradation remains
a successful model; process and protocol failures become stable structured
errors. Managed runs emit one deterministic JSON object on writable stdout and
nothing on stderr. A broken stdout exits as a silent managed failure. The full
versioned contract is in
[bridge-contract.md](bridge-contract.md).

The repository-owned `switchboard-open` executable is the QML presentation
adapter. It accepts either one canonical session key or one canonical
project/location/provider target, one Claude history target, or one stoppable
Claude session. It generates a request UUID for presentation preparation, asks
the same validated bridge layer for a plan or action, and performs only the
advertised operation:

1. `focus` matches a niri window by a SHA-256-derived Wayland application ID.
   An adopted pre-Switchboard pane can instead match the exact tmux workspace
   title prefix plus the short host suffix.
2. `switch` calls `swbctl select-surface` through the bridge, then focuses that
   window.
3. `attach` launches the configured Ghostty executable through
   `systemd-run --user --scope --collect --quiet --` and runs only
   `swbctl attach-surface <surface-id>` inside it.
4. `blocked` returns the core-authored structured error and starts nothing.
5. `stop` delegates the canonical session key to core and accepts only a
   validated stopped/already-stopped action or structured blocked error.

If focus fails, the helper prepares again with the same request ID and
`can_focus_desktop=false`. Only an attach plan is accepted as fallback. The
desktop token is never used as an application ID directly; its SHA-256 digest
produces an opaque valid class. DMS owns niri and Ghostty behavior, while core
owns the tmux locator, provider command, lease, and duplicate-runtime check.

## Launcher data flow

DMS calls `getItems(query)` synchronously, so the launcher answers only from an
in-memory cache. `getItems(query)` schedules work with `Qt.callLater` but never
starts or waits for a process in the synchronous read path. The persistent QML
`Process` invokes `switchboard-bridge` asynchronously with an argv array:

1. Return filtered items from the current cache immediately.
2. Start with a retained read; start or coalesce one full refresh when the
   snapshot's source timestamp is older than the configured interval. An
   equivalent or weaker request is absorbed by the active run; a stronger full
   refresh or a changed settings generation queues exactly one follow-up run.
3. Let the bridge enforce its configured `swbctl` deadline. A QML deadline two
   seconds later marks the generation failed and uses Quickshell 0.3's verified
   `Process.signal(15)` method; late output from an expired generation is
   ignored. Quickshell 0.3 can report `runningChanged(false)` without
   `exited` or collector completion when a process cannot start, so a guarded
   deferred transition records one managed start failure, completes that
   generation, and releases any queued run.
4. Parse the complete versioned bridge envelope and validate the frontend model
   before atomically replacing cache state.
5. Retain the last-good snapshot after command, timeout, parse, or validation
   failures and expose the current structured failure alongside retained rows.

The settings surface uses DMS 1.5.0's `PluginSettings`, `DankTextField`, and
`SliderSetting` components. It persists `swbctl`, `terminal`, `timeout_ms`, and
`refresh_seconds` through the exact `loadPluginData(pluginId, key,
defaultValue)` and `savePluginData(pluginId, key, value)` service methods. The
manifest's `settings_read`, `settings_write`, and `process` permissions are the
complete permission set required by this behavior.

State presentation remains honest. Missing observations and stale data are
unknown; the UI does not infer liveness or activity. The bridge turns an absent
provider capability into a neutral Codex or Claude capability, which the
launcher renders as unknown rather than unavailable. Session rows display
source-authored activity, runtime presence, resumability, and attachment
values. For every declared local tmux location, the bridge projects one explicit
Codex target and one explicit Claude target. Launch targets contain stable IDs,
the bounded provider enum, and bounded display fields, never paths or provider
argv.
Session and launch-target selection always runs asynchronously through the
validated action helper.
Structured action failures become the current failure item; a success schedules
a full snapshot refresh.

The bridge remains the authoritative full Snapshot and projected-model
validator. Its private model v2 projects both local Codex and Claude session
rows and exactly one ordered capability record for each provider. The pure
`SwitchboardModel.js` consumer additionally validates the versioned envelope,
required display shape, provider identity coherence, and state enums while
accepting unknown forward-compatible fields. It projects stable
item IDs from session, project, and location identities; orders sessions by
recency and launch targets canonically; and searches name, path, project,
location, host, and stable identity. Claude launch targets additionally produce
one native-history action, while only confirmed live launch-owned Claude rows
with an active current tmux surface produce stop actions. The model carries only
the boolean `canStop`; core revalidates ownership at execution time.

The launcher surface emits `itemsChanged()` after state changes, but DMS 1.5.0's
`AppSearchService.getPluginItemsForPlugin()` directly invokes `getItems()` and
does not connect that signal. A background refresh therefore becomes visible
only when the launcher is reopened or the query changes. The cache does not
promise live in-place updates.

## Non-goals

This repository does not currently provide:

- SSH support or remote-host orchestration
- provider hooks or liveness inference
- arbitrary working-directory launch or project-catalog editing
- direct tmux locator construction or provider launch logic
- non-niri compositors or non-Ghostty terminal adapters
- a chezmoi cutover or configuration migration
- a rich widget

It also does not recreate provider, tmux, SSH, compositor, terminal, or config
management logic inside QML. The separate legacy `agentSessions` plugin remains
available for remote behavior; this integration neither disables nor modifies
it.
