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
limitation. Live installation and an actual DMS runtime exercise were deferred
to Phase 4.

## Phase 4: development install and live DMS integration (complete)

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

Acceptance was completed on DMS 1.5.0, Quickshell 0.3.0, and Qt 6.11.1. The
reversible checkout symlink was discovered, enabled, reloaded, disabled, and
removed through installed DMS paths without disturbing legacy plugins. Real
shell process samples captured both `swbctl snapshot --json` and
`swbctl snapshot --reconcile full --json` through the configured core
virtual-environment executable. The installed-import component harness proved
settings focus and height, query filtering, cache replacement, last-good
retention, and both failure recovery paths. The sanitized reproducible runbook,
versions, selected evidence, restoration boundary, and limitations are in
[live-integration.md](live-integration.md).

CI remains the dependency-free Python/JavaScript/static baseline. The live
Quickshell harness requires an active display and an installed DMS tree, and
there is no reproducible standalone headless `qmltestrunner` invocation for
DMS's special `qs.*` config imports. No headless QML or live-shell CI coverage
is claimed.

## Phase 5: existing local Codex actions (complete)

This phase implements the DMS portion of Agent Switchboard Phase 3A:

- The bridge independently validates PresentationPlan v1 and exposes exact
  fixed-argv prepare/select modes.
- Session items retain only the canonical session key and source host display
  name needed by the asynchronous action process.
- `switchboard-open` generates one request ID, executes focus/switch/attach
  plans, and returns one bounded `actionVersion: 1` result.
- Managed Ghostty application IDs are SHA-256 derivations of opaque desktop
  tokens. Adopted panes retain exact tmux-title-plus-host fallback matching.
- A failed niri focus reprepares with the same request ID and
  `can_focus_desktop=false`; only a core-authored attach plan is accepted.
- Ghostty is detached from the DMS service cgroup through a collected user
  systemd scope and runs only `swbctl attach-surface <surface-id>`.
- QML remains shell-free, reports structured action failures, prevents
  concurrent selections, and schedules a full refresh after success.

Acceptance completed with 109 Python tests and 18 JavaScript behavior groups,
QML formatting, Ruff, package Pyright, and diff checks. Live adoption focused
an existing Ghostty window without changing niri window count or the tmux
server PID. A DMS plugin reload and `sb:` query kept both DMS and tmux service
identities stable and did not modify the separate legacy `agentSessions`
plugin. After the five Codex hooks were trusted, parked-session startup and
same-session reopen also passed without duplicating the managed window or
Codex runtime; the final evidence is recorded in
[live-integration.md](live-integration.md).

## Roadmap handoff

This repository's existing-local-Codex action slice is complete. Cross-repo
development returns next to Agent Switchboard Phase 2B for Claude discovery,
hooks, supervisor/process liveness, and normalized runtime truth. DMS does not
add Claude actions before those core capabilities exist.

After Phase 2B, DMS resumes with Phase 3B parity for project-aware new local
Codex sessions, then Phase 3C parity for core-authored Claude workspace and
session presentation plans. The legacy `agentSessions` plugin remains the
Claude and remote fallback until those paths pass equivalent live validation.

## Final audit and local handoff

- Re-run all unit, JSON, shell, QML, fixture, and whitespace checks.
- Review the complete worktree against the boundary and non-goals.
- Make small local commits with clear messages only after review.
- Do not push; leave remote publication to an explicit later instruction.

Claude, SSH, provider hooks/liveness, project/new-session actions, direct tmux
locator or provider-launch logic, non-niri/non-Ghostty adapters, chezmoi
cutover, and a rich widget remain non-goals. The legacy plugin remains the
Claude and remote fallback.
