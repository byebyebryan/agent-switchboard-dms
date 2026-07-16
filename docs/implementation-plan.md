# Implementation plan

## Phase 0: discovery and contract lock (complete)

Discovery established the public boundary before implementation:

- DMS loads launcher plugins synchronously and requires `getItems(query)` to
  return immediately.
- The DMS settings host expects a visual/focus-capable root, reads its
  `implicitHeight`, and calls `forceActiveFocus()`.
- The integration boundary is a user-configured `swbctl` executable plus
  Snapshot v1 JSON. The plugin must not import internal Agent Switchboard
  modules or read its database directly.
- The only accepted commands are `swbctl snapshot --json`,
  `swbctl snapshot --reconcile full --json`, `swbctl list --json`, and
  `swbctl list --refresh --json`.
- The protocol fixture and provenance are locked to the source commits and
  digest recorded in `tests/fixtures/README.md`.

Verified caveat: DMS does not currently consume `itemsChanged()` or
`requestLauncherUpdate` for this launcher flow. A future background refresh
will therefore appear on reopen or query change, not as a promised live
in-place update.

Acceptance criteria were a documented, versioned boundary; a pinned synthetic
Snapshot v1 fixture; and explicit non-goals. Those criteria are complete.

## Phase 1: inert DMS scaffold (complete)

Deliver the smallest installable repository surface without runtime behavior:

- Provide the DMS manifest, MIT license, repository metadata, inert launcher
  and settings QML roots, architecture notes, tests, and baseline CI.
- Keep launcher `getItems(query)` synchronous and returning `[]`.
- Keep `executeItem(item)` an explicit no-op. Selection remains unavailable.
- Give settings an inert `FocusScope` root with `pluginService` and zero
  `implicitHeight`, compatible with the verified settings host contract.
- Invoke no process, ship no runtime Python package, and hardcode no local
  executable path.

Acceptance requires manifest and JSON parsing, static QML surface checks,
portable fixture provenance and digest verification, dependency-free unit
tests, shell syntax validation, meaningful whitespace checks, and successful
local `qmllint` when available. CI runs only the dependency-free baseline;
`qmllint` and `qmltestrunner` support are not claimed in CI yet.

## Phase 2: bounded Snapshot bridge, model, and tests (complete)

The read-only bridge is implemented behind the locked boundary:

- `switchboard_dms.protocol` parses Snapshot v1 defensively into a bounded,
  deterministic frontend-owned model, ignoring safe unknown fields while
  rejecting incompatible or sensitive envelopes.
- `switchboard_dms.process` drains both child pipes concurrently with strict
  time and byte limits and kills the isolated process group on every abnormal
  execution or cleanup exit.
- `switchboard_dms.bridge` constructs only the retained and full-refresh
  snapshot argv arrays, without shell parsing or machine-specific paths.
- The executable emits one deterministic versioned JSON envelope, preserves
  valid neutral/degraded provider state as success, and maps process, UTF-8,
  JSON, protocol, and serialization failures to stable bounded errors.
- Deterministic tests cover argv, schema/version handling, unknown fields,
  malformed output, nonzero exits, timeouts, process-group cleanup, pipe
  overflow, strict single-document framing, fixture projections, independently
  bounded protocol diagnostics, privacy boundaries, and serialized size.

Acceptance is satisfied by the fully tested bridge/model boundary with no QML
launcher behavior change, internal Agent Switchboard imports, or direct private
database reads. The exact contract is recorded in `docs/bridge-contract.md`.

## Phase 3: launcher, settings, cache, and degradation (complete)

The bounded model is connected to the DMS surfaces:

- DMS `PluginSettings` persists one executable token plus bounded timeout and
  refresh controls; no provider, transport, project-action, or remote-host
  configuration was added.
- `getItems(query)` synchronously filters the in-memory last-good model and uses
  `Qt.callLater` to schedule retained or full-refresh work outside the read.
- One persistent `Process` coalesces runs. Only a complete exit-zero bridge
  success with a validated model replaces the cache; failures remain explicit
  while last-good rows stay visible.
- Neutral, stale, and source-authored state remain honest. Missing observations
  do not create liveness or activity claims.
- Pure JavaScript tests cover deterministic order, stable IDs, each required
  search surface, current, stale, loading, degraded, neutral, empty,
  retained-error, and forward-compatible models. Static tests lock the
  asynchronous QML boundary.
- `executeItem(item)` remains a safe no-op.

Acceptance is satisfied by cached reads with no synchronous process execution,
last-good retention, explicit degradation, honest unknown state, and refreshed
cache results on reopen or query change under the verified DMS 1.5.0
limitation. Live installation and an actual DMS runtime exercise remain Phase
4 work.

## Phase 4: development install and live DMS integration

- Document a reversible development install and removal flow.
- Exercise plugin discovery, enablement, reload, disablement, and removal with
  the supported DMS commands.
- Verify the launcher trigger, query behavior, settings focus/height behavior,
  process lifecycle, cache replacement, and failure presentation in live DMS.
- Add CI QML validation only after its toolchain and invocation are proven in
  the repository; do not imply `qmltestrunner` coverage before it exists.
- Record the exact DMS and Qt versions used for integration evidence.

Acceptance requires a clean install/reload cycle, no DMS load errors, a usable
settings host, evidence for both success and degraded paths, and CI that makes
only checks it actually performs.

## Final audit and local handoff

- Re-run all unit, JSON, shell, QML, fixture, and whitespace checks.
- Review the complete worktree against the boundary and non-goals.
- Make small local commits with clear messages only after review.
- Do not push; leave remote publication to an explicit later instruction.

Claude, SSH, hooks/liveness, project actions, tmux creation, niri, Ghostty,
chezmoi cutover, and a rich widget remain non-goals throughout this plan.
