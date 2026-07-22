# View-Entry Clean-Break Plan

Date: 2026-07-21

Status: implemented; installed two-host activation pending

Target adapter release: `0.5.0`

Paired core target: Agent Switchboard `0.3.0`, Phase 6E

## Outcome

The DMS adapter becomes a small entry and structural-recovery picker for
durable Agent Switchboard views. The current task-first Fleet/Snapshot model is
deleted during one coordinated core/DMS activation. There is no compatibility
mode, cache migration, command alias, plugin-reload cutover, or mixed-version
operation.

The installed `0.4.x` adapter remains task-first until activation and is
documented by `architecture.md`, `bridge-contract.md`, and the historical
implementation/live-integration records. This plan is the only future contract.
The paired core `docs/state-contract.md` is normative for ownership, routing,
revisions, desktop leases, transition state, and recovery actionability.

## Boundary

DMS owns only desktop presentation:

- run one configured local `swbctl` through fixed argv;
- validate a bounded core-authored navigator state;
- render View, Project, and structural Recovery rows;
- focus one matching niri window by opaque desktop token;
- launch configured Ghostty only under a core-issued attach lease;
- retain one last-good bounded entry model with source provenance; and
- report structured bridge/desktop failures.

Core owns every project/frame/view/session/runtime/tmux/SSH decision. DMS never
reads the registry/config, invokes Git/SSH/tmux/provider commands, parses
transcripts, constructs navigation, chooses a frame, receives a filesystem or
tmux path, or converts a desktop miss into semantic replay.

## Replacement Source Contract

The bridge reads exactly:

```text
[EXECUTABLE, "state", "navigator", "--json"]
[EXECUTABLE, "state", "navigator", "--refresh", "--json"]
```

`NavigatorState v1` contains core-derived host-qualified views, project entry
routes, structural recovery, reachability/staleness, bounded warnings, and
truncation. DMS does not join records or interpret provider/task/session state.
Its top-level `generationId` is the local generation and becomes the entry
model's `sourceGenerationId`. Each host row retains its owner generation and an
explicit stale bit. Core supplies the view title, breadcrumb, activity,
attention, transition/control state, and last activity; DMS does not recreate
those semantics.

Each activation calls one core route with a new UUID:

```text
[EXECUTABLE, "view", "open", "--host", HOST_ID,
 "--view", VIEW_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]

[EXECUTABLE, "view", "open", "--host", HOST_ID,
 "--project", PROJECT_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]

[EXECUTABLE, "view", "recover", "--host", HOST_ID,
 "--recovery", RECOVERY_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]
```

Exactly one target is accepted. `--view` revalidates and presents that view
as-is. `--project` is explicit navigation: core resolves the workspace's owning
view; retains its active descendant when it already shows that project;
otherwise routes to the most recently focused open descendant or workspace;
and uses live revision CAS. If no placement exists, concurrent requests
single-flight on one navigator-mode DMS view. A Project click never focus-only
opens a view still showing an unrelated project.

Recovery selection executes only the exact core-authored actionability class:

- `safe_auto`: run the bounded idempotent repair;
- `open_view`: present the owning view's recovery panel; or
- `manual`: show information and require the user to enter core.

Checkout/background ambiguity is always `manual` or `open_view`, never a DMS
mutation.

## PresentationDirective v1

Core commits or revalidates semantic navigation before returning:

```text
directiveVersion = 1
requestId
hostId
kind                  focus | attach | blocked
viewId                required for focus/attach
viewRevision          required for focus/attach
desktopToken          required for focus/attach
leaseExpiresAt        required only for attach
error                 required only for blocked
```

There is no `switch` desktop directive. `focus` asks DMS to find the canonical
window. `attach` carries the one expiring desktop lease for that view. `blocked`
contains one bounded diagnostic/recovery route. No directive contains a tmux
target, provider command, checkout path, SSH target, or semantic instructions.

On focus miss, DMS repeats the same request ID with desktop focus disabled:

```text
[EXECUTABLE, "view", "open", ..., "--request-id", UUID,
 "--no-focus-desktop", "--can-launch-terminal", "--json"]
```

Desktop presentation capability is excluded from the normalized semantic
request fingerprint. Core therefore reuses the committed navigation, grants at
most one attach lease, and never repeats a project/frame mutation. A different
concurrent semantic request cannot claim the lease.

Ghostty starts only the leased attach command:

```text
[EXECUTABLE, "view", "attach", "--host", HOST_ID,
 "--view", VIEW_ID, "--request-id", UUID]
```

Core consumes and revalidates the lease, owns local/remote routing, and attaches
the exact host-local view. A remote selection therefore opens a separate
SSH-backed view rather than placing a remote pane in a local view.

## Entry Model v1

The adapter resets private numbering rather than extending model v5, bridge v4,
or action v4:

```json
{
  "bridgeVersion": 1,
  "ok": true,
  "model": {
    "modelVersion": 1,
    "sourceNavigatorVersion": 1,
    "sourceGenerationId": "opaque",
    "generatedAt": 0,
    "localHostId": "uuid",
    "hosts": [],
    "views": [],
    "projects": [],
    "recoveries": [],
    "warnings": [],
    "truncation": {}
  }
}
```

### Views focus as-is

One row per retained view contains only `(HostId, ViewId)`, active project/frame
title and breadcrumb, actual navigator/direct mode, ready/transitioning/degraded
state, bounded attention, last activity, and opaque presentation capability.

Selecting a View never changes its active frame, project, or mode. It focuses or
attaches exactly that durable view. Ordinary extra tmux clients intentionally
share its cursor.

### Projects navigate

One row per host-qualified project route contains project name, host display,
workspace availability, current owner/route summary, and bounded issue state.
Selecting it asks core to navigate the workspace's owning view to the project's
current open descendant or workspace, or create the one reserved view. DMS does
not select checkout/provider, create a task, or infer a target. Missing defaults
route to core-authored structural recovery or focused project settings.

### Recovery is structural

Rows cover failed/stuck transitions or control turns, invalid view ownership,
live surfaces without a valid placement, WorkContext claim conflicts, missing
tmux containers, and cutover/runtime repair. Every row carries `safe_auto`,
`open_view`, or `manual` actionability from core.

Needs-input, working, ready, stopped, stale, and offline remain View badges.
Provider history and closed frames stay inside Switchboard panels.

### Removed model concepts

Entry model v1 has no open/closed task list, Inbox, task/provider creation,
close/reopen/history/stop actions, session/surface IDs, checkout paths, provider
argv, or task/Inbox truncation counters.

## QML Presentation

The launcher exposes:

1. `Views` (default)
2. `Projects`
3. `Recovery` (only when nonempty)

Search matches only bounded visible text and never manufactures rows from a
query. View rows lead with frame title; project and recovery rows have distinct
badges. Context actions remain limited to View focus/show-navigator, Project
open, exact Recovery action, settings, and refresh. There are no task, session,
provider, or copied-ID actions.

The replacement may retain the three-process shape but rewrites all contracts:

- `switchboard-bridge`: state read/refresh and entry-model validation;
- `switchboard-open`: core route plus niri/Ghostty presentation; and
- `SwitchboardLauncher.qml`: synchronous last-good projection and scheduling.

The separate project-manager wrapper is removed. All helpers use fixed argv, no
shell, bounded streams/time, process groups, kill/reap cleanup, strict JSON
framing, and one canonical output record.

## Desktop Identity and Single Flight

The DMS-managed desktop application identity hashes the directive's opaque
`desktopToken` with its owner HostId. It is stable while frames/provider panes
change; provider/session identities never participate, and cached
NavigatorState supplies no identity capability. Exactly one
canonical DMS-managed window may exist per view. Ordinary external
tmux clients are supported but do not receive or share this DMS application
identity. Focus considers exact matching DMS windows only. Zero matches permits
the leased fallback; one focuses; more than one returns
`ambiguous_desktop_windows` and never launches a third.

Core permits one unexpired desktop attach lease per view. Same-request fallback
may claim it; a different request receives `desktop_launch_in_progress`. DMS
does not use local timing or window count as semantic idempotency authority.

## Cache v1

The new key is `last_good_switchboard_entry_model_v1`. Its envelope includes
adapter contract, source generation, generated time, validation version, and
the bounded model. The adapter never reads or transforms
`last_good_model_v5_bridge4`.

Cold and warm instances fully validate cache provenance before use. Complete
bridge success atomically replaces it. Failure may retain last-good entries
with an explicit status row; stale/offline state never authorizes mutation. A
source generation change invalidates cached action capability and requires a
fresh read before selection.

## Staged Cutover Behavior

Core's imported generation initially reports `cutover_staged`. DMS may read and
validate NavigatorState v1, build/cache entry model v1, and exercise nonmutating
desktop diagnostics. Every View, Project, Recovery mutation, attach, provider
action, and hook path must return the stable staged diagnostic. DMS must display
that state and must not convert it into a launch fallback.

This staged read is acceptance evidence for the paired versions, not a period of
mixed operation. The old adapter is already disabled and closed; only the new
adapter reads the new staged core.

## Clean-Break Activation

Activation is paired with core Phase 6E:

1. Back up old DMS state and record installed plugin/package hashes.
2. Disable the old Switchboard plugin and close every old picker/window before
   core replacement or import.
3. Install paired core `0.3.0` and DMS `0.5.0` artifacts inactive. The new DMS
   does not read the old cache key.
4. Import the new core generation in staged mode.
5. Cold-restart DMS, enable only the new plugin, and prove entry model v1 was
   loaded from NavigatorState v1. A plugin reload is not sufficient because it
   can retain the old QML/JavaScript engine and module cache.
6. Validate local, retained-remote, cold-cache, warm-cache, focus-diagnostic,
   and staged-mutation-blocking paths.
7. Commit the core cutover only after paired-version and cold-start evidence
   passes. Reinstall core hooks after commit.
8. Open the first view and prove niri focus, leased Ghostty attach, same-view
   dedup, Projects navigation, Views focus-as-is, recovery, offline retention,
   and remote presentation.
9. Delete only the backed-up old Switchboard cache/artifacts after acceptance;
   unrelated DMS plugin state remains untouched.

Before the core cutover commit, the coordinated rollback restores the old core
pointer/packages, old DMS package/cache/settings, and hook configuration. After
commit there is no automatic downgrade; recovery is forward-only or an explicit
operator-led offline restore.

The paired one-shot executor stages both hosts before either commit and records
exact commits, artifact hashes, observed provider versions, generation IDs,
cold-start identity, read hashes, and named checks as `CutoverEvidence v1`.
Snap, in role `remote_owner`, commits first; local, in role `desktop_primary`,
commits second while DMS and hooks remain disabled. DMS is enabled only after
both commits and hook installation succeed.

## Clean-Break Deletion

Delete rather than deprecate:

- Fleet/Snapshot parsers and task/Inbox projection;
- model-v5 JavaScript and warm-cache compatibility branches;
- prepare-open/task/history/close/stop argv builders;
- project-manager wrapper and singleton identity;
- task/session/history/stop desktop functions and flags;
- task categories, creation rows, provider badges, old state precedence, and
  secondary actions;
- bridge/action v4 and model-v5 fixtures/tests; and
- active `0.4.x` README/architecture/contract claims.

Historical implementation/evidence remains in Git history or a non-packaged
archive only.

## Delivery Sequence

1. Accept core HostState v1, NavigatorState v1, PresentationDirective v1, state
   machines, view shell, and one-child workflow through private harnesses.
2. Implement DMS bridge/model v1 and strict fixtures, including staged state,
   request fingerprint, attach lease, ambiguity, and recovery actionability.
3. Replace QML with Views/Projects/Recovery and the provenance-aware cache.
4. Run bounded process failure, cache cold/warm, QML, desktop single-flight,
   privacy, and incompatible-generation tests.
5. Rehearse paired artifacts in isolated XDG roots with local/remote fixtures
   and a real DMS cold restart.
6. Execute the coordinated Phase 6E activation above.

## Acceptance

- Empty query shows Views, never tasks or sessions.
- View activation never changes its active frame, project, or mode.
- Project activation never leaves an unrelated project visible; it targets the
  current owned descendant/workspace and concurrent opens converge.
- Direct-mode Views remain direct when focused from DMS.
- Recovery obeys `safe_auto | open_view | manual` exactly.
- Focus miss repeats no semantic mutation and consumes at most one attach lease.
- Ambiguous exact desktop matches block without launching another window.
- Ordinary tmux clients do not collide with the canonical DMS desktop identity.
- Remote selection focuses/starts a separate SSH-backed host-local view.
- Cache cold/warm and bridge failure paths reveal no old model or authority.
- Staged mode renders state while all mutation/attach paths stay blocked.
- Built artifacts contain no Fleet/Snapshot/task/Inbox/close/reopen/history/stop
  action path.
- DMS `0.5` cannot run with core `0.2`, and core `0.3` rejects DMS `0.4`, with
  one bounded incompatible-generation diagnostic.
- Clean activation evidence comes from disabling/closing the old adapter and a
  cold DMS restart, never a plugin reload.
