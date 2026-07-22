# View-Entry Clean-Break Plan

Date: 2026-07-21

Status: accepted replacement design; implementation pending

Target adapter release: `0.5.0`

Paired core target: Agent Switchboard `0.3.0`, Phase 6E

## Outcome

The DMS adapter becomes a small entry and structural-recovery picker for
durable Agent Switchboard views. The current task-first Fleet/Snapshot model is
deleted during one coordinated core/DMS activation. There is no compatibility
mode, cache migration, command alias, or mixed-version operation.

The installed `0.4.x` adapter remains task-first until that activation and is
documented by `architecture.md`, `bridge-contract.md`, and the historical
implementation/live-integration records. This plan is the only future contract.

## Boundary

DMS continues to own only desktop presentation:

- run one configured local `swbctl` through fixed argv;
- validate a bounded core-authored navigator state;
- render view, project-entry, and recovery rows;
- focus a matching niri window by opaque desktop token;
- launch configured Ghostty when no matching view client exists;
- retain one last-good bounded entry model; and
- report structured bridge/desktop failures.

Core owns every project/frame/view/session/runtime/tmux/SSH decision. DMS never
reads the registry/config, invokes Git/SSH/tmux/provider commands, parses
transcripts, constructs a frame transition, or receives a filesystem path.

## Replacement Source Contracts

The bridge reads exactly:

```text
[EXECUTABLE, "state", "navigator", "--json"]
[EXECUTABLE, "state", "navigator", "--refresh", "--json"]
```

The source is `NavigatorState v1`. It already contains the core-derived,
host-qualified views, project entry routes, structural recovery rows, host
reachability/staleness, bounded warnings, and truncation. DMS does not join
Snapshot records or interpret provider/task/session state.

Selecting an entry invokes one of:

```text
[EXECUTABLE, "view", "open", "--host", HOST_ID,
 "--view", VIEW_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]

[EXECUTABLE, "view", "open", "--host", HOST_ID,
 "--project", PROJECT_ID, "--new-mode", "navigator",
 "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]

[EXECUTABLE, "view", "recover", "--host", HOST_ID,
 "--recovery", RECOVERY_ID, "--request-id", UUID,
 "--can-focus-desktop", "--can-launch-terminal", "--json"]
```

Exactly one target is accepted. Project activation focuses a live owning view
as-is; only an unowned project workspace may create a navigator-mode view.

The returned `ViewAction v1` kind is `focus`, `switch`, `attach`, or `blocked`.
It contains `HostId`, `ViewId`, an opaque desktop token, request ID, and bounded
error. It never contains a tmux target, provider command, checkout path, or SSH
target.

Ghostty starts only:

```text
[EXECUTABLE, "view", "attach", "--host", HOST_ID,
 "--view", VIEW_ID]
```

Core owns local/remote routing. A remote selection therefore launches a
separate SSH-backed host-local view rather than switching a local tmux client
into remote content.

## Entry Model v1

The adapter resets its private contract numbering rather than extending model
v5/bridge v4/action v4:

```json
{
  "bridgeVersion": 1,
  "ok": true,
  "model": {
    "modelVersion": 1,
    "sourceNavigatorVersion": 1,
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

### Views

One row per retained view contains only:

- `(HostId, ViewId)` identity;
- active project/frame title and breadcrumb summary;
- actual `navigator` or `direct` mode;
- ready/transitioning/degraded state;
- needs-input/working/ready/offline attention;
- last activity; and
- opaque presentation capability flags.

Selecting a view focuses/attaches it without changing its active frame or
mode. Multiple clients on that view intentionally share its cursor.

### Projects

One row per host-qualified project entry route contains project name, host
display, workspace availability, and bounded issue state. Selecting it:

1. focuses the existing owning view as-is when one exists;
2. otherwise creates one navigator-mode view at the durable workspace; or
3. reports the core-authored blocked/recovery action.

DMS neither selects a checkout/provider nor creates a task. Missing defaults
route to structural recovery or the view's focused project settings.

### Recovery

Only structural failures appear:

- blocked/failed view transitions;
- live managed surfaces without a valid owning view;
- checkout/work-context claim conflicts; and
- missing or inconsistent tmux view containers.

Needs-input, working, ready, stopped, stale, and offline are view badges, not
duplicate Recovery rows. Provider history and closed frames remain inside the
Switchboard navigator/panels.

### Removed model concepts

Entry model v1 contains no:

- open/closed tasks;
- Inbox sessions;
- task/provider creation rows;
- task close/reopen actions;
- provider history picker action;
- managed runtime stop action;
- session/surface IDs;
- checkout/worktree paths;
- provider argv; or
- task/Inbox truncation counters.

## QML Presentation

The launcher exposes three native categories:

1. `Views` (default)
2. `Projects`
3. `Recovery` (only when nonempty)

Search matches bounded visible view/project/recovery text. It never manufactures
creation rows from a query. View rows show the frame title as primary text and
project, optional remote host, breadcrumb/mode, state, and age as secondary
text. Project and recovery rows use distinct badges rather than provider badges.

Context actions remain small:

- view: focus/open, show navigator (mode change through core), copy no IDs;
- project: open workspace;
- recovery: execute the exact core-authored recovery action; and
- global: settings and refresh.

There are no task/session/provider actions in QML.

## Process Split

The replacement may retain the three-process shape but rewrites every contract:

- `switchboard-bridge`: state read/refresh and entry-model validation;
- `switchboard-open`: view/project/recovery prepare plus niri/Ghostty execution;
- `SwitchboardLauncher.qml`: synchronous last-good model projection and process
  scheduling.

The separate project-manager wrapper is removed. Project entry and focused
project settings belong to the owning Switchboard view. No DMS process waits
for a full-screen task manager to exit.

All helpers use fixed argv, no shell, bounded stdin/stdout/stderr/time, process
groups, kill/reap cleanup, strict JSON framing, and canonical one-record output.

## Desktop Identity

Desktop application identity hashes `(HostId, ViewId)`. It is stable while the
view swaps provider panes and frames. Equal provider/session/surface identities
on another host cannot collide.

DMS requests focus first. On a miss, it repeats the same request ID with
desktop focus disabled and receives an attach action for the same view. Core
prevents a concurrent fallback from creating a second view.

## Cache and Reload

The new cache key is generation-specific, for example
`last_good_switchboard_entry_model_v1`. The adapter never reads or transforms
`last_good_model_v5_bridge4`.

Cold and warm instances fully validate the stored model before use. A complete
bridge success atomically replaces it. Failures retain last-good views with an
explicit status row. Offline/stale host records remain visible but never
authorize mutation.

Activation deletes only the known old Switchboard cache key after its backup.
Unrelated DMS plugin state is untouched.

## Clean-Break Deletion

The activation commit deletes rather than deprecates:

- Fleet/Snapshot parsers and task/Inbox projection;
- model-v5 JavaScript and warm-cache compatibility branches;
- prepare-open/task/history/close/stop argv builders;
- project-manager wrapper and its singleton app identity;
- task/session/history/stop desktop functions and CLI flags;
- QML task categories, creation rows, provider badges, state precedence, and
  secondary actions;
- bridge/action v4 and model-v5 fixtures/tests; and
- old README/architecture/contract claims from the active document set.

The `0.4.x` implementation and evidence remain available in Git history and a
non-packaged archive only.

## Delivery Sequence

1. Land/accept core HostState v1, NavigatorState v1, ViewAction v1, view shell,
   navigator, and one-child workflow through private/test entrypoints.
2. Implement DMS entry-model v1 bridge and desktop helpers against fixtures.
3. Replace QML with Views/Projects/Recovery and a new cache key.
4. Run privacy-safe component, process-failure, cache cold/warm, and QML tests.
5. Rehearse paired installed core/DMS in isolated XDG state with local and
   retained-remote hosts.
6. During Phase 6E, quiesce old core runtimes, perform the registry/config
   cutover, install core `0.3.0`, then install/reload DMS `0.5.0`.
7. Prove niri focus, Ghostty launch, same-view dedup, offline retention,
   recovery, and remote view presentation before removing backups.

## Acceptance

- Default empty query shows existing views, not tasks or sessions.
- A view activation never changes its active frame.
- A project with an owner focuses that owner as-is.
- An unowned project creates exactly one navigator-mode view under concurrent
  focus fallback.
- Direct-mode views remain direct when opened from DMS.
- Structural recovery actions are core-authored and host-qualified.
- Needs-input/offline remain ordinary view badges.
- Remote selection starts/focuses a separate SSH-backed view.
- Cache cold/warm and bridge failure paths reveal no old model.
- Built sources contain no Fleet/Snapshot/task/Inbox/close/reopen/history/stop
  action path.
- The adapter cannot run with core `0.2`, and core `0.3` cannot run the old
  adapter; both fail closed with a bounded incompatible-generation diagnostic.
