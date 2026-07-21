# Implementation plan

> Historical chronology: Phases 1 through 3C below describe the retired
> Snapshot v1/location/session-list implementation. Phase 4D replaces that
> runtime with Snapshot v2, repositories/checkouts/tasks, frontend model v3,
> native project/Inbox/Closed categories, atomic `prepare-task`, and context
> actions. Phase 4E hardens that frontend against shell-local JavaScript cache
> retention and cold QML instances. Phase 5 advances the current adapter to
> Fleet v1, frontend model v4, bridge/action v3, and host-qualified local-core
> actions. Phase 6 exposes the core-owned local project catalog through a
> dedicated DMS category and focused TUI handoff. Phase 7 makes task close a
> single state-first action, adds one-action reopen/open, and advances the
> adapter to model v5, bridge/action v4, and `0.4.0`. See `architecture.md` and
> `bridge-contract.md` for the current core
> `0.2.0` development / adapter `0.4.0` contract.

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

This repository's existing-local-Codex action slice is complete. Phase 3B added
the implementation for project-aware new local Codex sessions: the bridge
projects bounded launch targets, invokes core `prepare-new` with stable
project/location IDs, and reuses the validated focus/switch/attach path. Live
DMS acceptance is complete against one explicitly configured local project.

The implementation checkpoint on 2026-07-16 passed 113 Python tests, 19
deterministic JavaScript behavior groups, QML formatting, Ruff, package
Pyright, and diff checks. The installed DMS 1.5.0 development plugin initially
projected one declared Codex/tmux launch target alongside six retained sessions
with no warnings. Core's controlled no-client live path expired without
changing the Codex process or tmux session counts. After the user manually
trusted the five installed Codex hooks, positive start/bind/reopen acceptance
created one configured Codex thread and moved the live counts from 2 to 3
Codex processes, 4 to 5 tmux sessions, and 1 to 2 niri windows. The refreshed
bridge projected seven sessions and the same one privacy-safe launch target
with no warnings. Reopening returned `focused` for the existing surface with
all counts unchanged. The initial acceptance remained at the empty prompt past
the original 30-second attachment lease, so exact missed-hook reconciliation
supplied the binding. Core review added a distinct bounded five-minute
provider-identity grace after attachment; a second no-write turn completed
without hook errors. The adapter never bypassed Codex's provider-owned trust
boundary.

Core Phase 2B is complete: Snapshot v1 now carries hook-known Claude sessions,
Agent-View-disabled capability checks, process/tmux liveness, and normalized
foreground runtime truth.

The first Phase 3C increment advanced the adapter's private frontend model to
version 2. It projects bounded local Codex and Claude session rows, one ordered
capability record per provider, and provider-attributed warnings. A canonical
Claude session key follows the existing asynchronous `prepare-open` and
focus/switch/attach path without exposing provider argv or tmux locators to
DMS.

The second Phase 3C increment projects distinct Codex and Claude launch targets
for every declared local tmux location. QML carries only project, location, and
provider identities; the bridge invokes core `prepare-new --provider`, and the
desktop helper reuses the existing validated focus/switch/attach path. Core
continues to own provider argv, cwd, tmux, leases, and hook binding. Live Claude
start/bind/reopen acceptance passed against the installed core and retained DMS
bridge with no prompt or model turn; the evidence is recorded in
[`docs/live-integration.md`](live-integration.md).

The final Phase 3C increment adds Claude's native history picker and safe
managed-runtime stop. The bridge invokes public `prepare-history` and
`stop-session` commands with fixed argv, validates PresentationPlan v1 or
SessionAction v1 independently, and never receives picker rows, provider argv,
or tmux locators. The frontend derives only a conservative `canStop` boolean
from a confirmed current live Claude surface; core revalidates launch, surface,
tmux, PID/birth, UID, and process-group ownership before acting. Installed
selection, stop, and picker-cancellation acceptance passed without a prompt or
model turn. A follow-up isolated desktop exercise also passed live niri focus
and same-window dedup while leaving the pre-existing Claude session untouched.
Remote hosts remain later work, and the legacy `agentSessions` plugin remains
only the untouched remote fallback.

## Phase 4D: repository-anchored projects and task-first DMS (complete)

Phase 4D replaces the location/session-list frontend contract instead of
layering aliases over it:

- Snapshot v2 carries projects, project/repository memberships, repositories,
  checkouts, first-class tasks, sessions, runtimes, and surfaces.
- The independent bridge validates that contract and emits bounded model v3
  project, task, and unassigned-Inbox records without paths or private Git
  identity.
- Native DMS categories are All tasks, one category per declared project,
  Inbox, and Closed. The default list contains open task rows plus one compact
  Inbox summary rather than every discovered provider session.
- A nonempty query within a project category offers Codex and Claude task
  creation. The desktop helper supplies a stable TaskId/request pair to core's
  atomic `prepare-task --create` path and reuses it across focus fallback.
- Task rows use provider-specific icons and concise project, nondefault
  checkout, state, and age metadata. Existing sessions remain unassigned until
  a human explicitly adopts them into a task.
- Claude history remains a project/checkout context action. Stop remains a
  conservative current-session action and is never implied by closing a task.

Implementation acceptance is 91 Python tests, 13 deterministic JavaScript
behavior groups, Qt 6 QML formatting, Ruff, package Pyright, and whitespace
checks. Guarded installed acceptance also passed against core `0.2.0`: the
adapter projected one explicit task and 49 Inbox sessions, exposed all native
categories and both provider creation choices, reopened the same managed tmux
session without another provider process, and produced no post-load component
or category warnings. Exact evidence is in `docs/live-integration.md`.

## Phase 4E: reload-safe picker cache and recovery (complete)

This corrective loop addresses two coupled DMS host behaviors observed after
the task-first rollout:

- Qt may retain a relative JavaScript import even after DMS reloads the QML
  component. Contract-stable filtering stays in `SwitchboardModelV3.js`, while
  bridge-envelope and persisted-cache validation moves into the cache-busted
  launcher component. Same-contract recovery no longer depends on replacing
  or reloading the retained JavaScript module.
- DMS 1.5.0 does not consume a launcher's `itemsChanged()` signal. Each valid
  model is now stored under a bridge/model-versioned DMS plugin-state key and
  fully revalidated synchronously when a new launcher instance starts. Normal
  shell starts and plugin reloads therefore have rows immediately while the
  asynchronous retained read runs.
- A first install or cleared/invalid cache remains subject to the DMS repaint
  limitation. One initial no-model parse failure retries automatically once;
  it does not create an unbounded process loop.
- The `switchboard-launcher` IPC target reports only bridge/model versions,
  idle/generation state, aggregate counts, and a stable failure code. This
  makes the exact live frontend state inspectable without exposing model rows,
  paths, host identity, or provider/session IDs.

Acceptance requires deterministic model/cache tests, the installed-import
component harness including cache round-trip, QML formatting, one real plugin
reload without a DMS restart, a healthy bounded IPC status, a persisted
versioned cache file, and no new Switchboard load or bridge failure in the
post-reload journal. It must not signal or restart Codex, Claude, or tmux
provider sessions. The final in-place plugin reload must keep the DMS service
PID stable; any one-time recovery restart for a preexisting development cache
must be recorded separately.

## Phase 5: Fleet federation and remote-owner routing (implementation complete)

This increment completes the DMS implementation half of Agent Switchboard's
pull-based SSH federation slice:

- the read bridge invokes only local `swbctl fleet --json` or
  `swbctl fleet --refresh --json`, validates Fleet v1 and every embedded
  Snapshot v2, and projects bounded frontend model v4;
- host state exposes only display name, stable HostId, reachability, staleness,
  and bounded errors; SSH targets and remote configuration remain in core;
- compatible ProjectIds share categories but retain eligible host-local
  default-checkout routes;
- task identity is `(HostId, TaskId)`, Inbox remains host-qualified, and remote
  rows add the host to their compact second line;
- project queries emit provider creation choices per eligible host and name the
  destination when several routes exist;
- every prepare, history, stop, select, and attach action carries the owning
  HostId through local `swbctl`; DMS never constructs SSH;
- returned plans for another host fail closed, and managed desktop identity
  hashes HostId with the opaque surface token; and
- the persisted cache moves to `last_good_model_v4_bridge3` and the physical
  JavaScript module moves to `SwitchboardModelV4.js`.

Automated acceptance covers two-host project merging, duplicate TaskIds on
different hosts, remote/offline/stale presentation, per-host creation rows,
never-seen remotes, malformed Fleet rejection, host-qualified exact argv,
plan-host mismatch, process cleanup, cache validation, and the private-state
Quickshell harness. Guarded installed acceptance must not restart or signal
active Codex, Claude, or tmux sessions. A true remote open/create/continuation
exercise additionally requires an explicitly configured reachable test host;
local-only acceptance must be recorded as such rather than presented as SSH
parity.

## Phase 6: local project catalog handoff (implementation complete)

This local-first follow-up closes the catalog UX gap before guarded two-host
acceptance:

- Projects is a static category even when the Fleet model is unavailable;
- the list contains Add Project, Manage Projects, and one compact row per
  project with a local route, while remote-only projects stay out;
- project rows carry only stable ProjectId and display metadata from model v4;
- `switchboard-projects` focuses or launches one Ghostty running
  `swbctl tui --view projects`, with optional selected-project or add-wizard
  startup;
- the wrapper lives only while that manager window exists, then requests one
  full bridge refresh and returns its Bridge v3 response to QML;
- QML fully revalidates and persists that result, making the next picker read
  useful despite DMS 1.5 ignoring `itemsChanged()`; and
- the physical JavaScript import moves to `SwitchboardModelV4Projects.js` so a
  warm plugin reload cannot retain the pre-Projects implementation.

Core remains the sole catalog writer and owns path inspection, atomic config
replacement, backups, archive blockers, and export/import. DMS does not edit
projects directly, receive paths, or carry mutation payloads. The wrapper
opens no provider or tmux session and leaves no daemon.

Deterministic implementation acceptance covers fixed terminal/TUI/refresh
argv, exact niri singleton matching, focus and launch paths, structured
failures, local-only projection, no-model Add/Manage access, QML cache handoff,
113 Python tests, 17 JavaScript behavior groups, QML formatting, Ruff, Pyright,
and whitespace checks. Guarded private-state and installed local acceptance is
recorded separately in `live-integration.md`.

## Phase 7: frictionless close and one-action reopen (complete)

- Open task rows expose **Close task** as their first secondary action through
  both keyboard navigation and the context menu.
- Close sends fixed argv to core with no stdin, confirmation, handoff editor,
  prompt injection, or model call.
- The desktop helper validates TaskCloseAction v2, projects cleanup disposition
  and warning through Action v4, and QML performs one full Fleet refresh.
- A cleanup warning does not keep the task in Open: DMS reports it and shows the
  Closed row with retained live/unknown runtime state.
- Activating a Closed row invokes `prepare-task --reopen` and opens/resumes it
  through the existing plan path in one action.
- Explicit stop becomes provider-neutral for source-projected eligible Codex
  and Claude rows; Claude history remains Claude-only.
- The physical frontend module and cache advance to
  `SwitchboardModelV5Close.js` and `last_good_model_v5_bridge4`.

Acceptance requires exact close/reopen argv tests, independent action and model
validation, keyboard/context action coverage, toast and refresh behavior,
closed-runtime rendering, warm-cache busting, and a guarded private-state
installed run. Live provider checks may use only test-owned blank sessions and
must not signal existing user sessions.

Acceptance completed on 2026-07-21. The deterministic lane passed 113 Python
tests, 17 JavaScript behavior groups, QML formatting, Ruff, Pyright, and
whitespace checks. The current core checkout was installed with its TUI extra.
A private-XDG project with one Open and one Closed task proved Action v4
`no_session` close, state-first reopen, and Bridge v4/model v5 projection
without launching a provider. The installed-import harness passed against that
private state, and the live plugin reported bridge 4/model 5 after the one
DMS-only restart required to admit the new JavaScript module path. The tmux
server PID and pane-PID fingerprint were identical across that restart.

## Final audit and publication

- Re-run all unit, JSON, shell, QML, fixture, and whitespace checks.
- Review the complete worktree against the boundary and non-goals.
- Keep core and DMS changes in separate reviewable commits.
- Publish both repositories only after the full gates pass and the user has
  explicitly authorized the push.

SSH construction inside DMS, direct DMS configuration writes, arbitrary
working-directory launch, direct tmux locator or provider-launch logic,
non-niri/non-Ghostty adapters, chezmoi cutover, and a rich widget remain
non-goals for this increment.
