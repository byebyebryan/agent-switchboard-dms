# Architecture

## Ownership boundary

The plugin is a strict frontend for one user-configured local `swbctl`. Core
owns registries, provider discovery, Git/worktree discovery, tmux, and every
SSH command. The DMS adapter must not import internal Agent Switchboard
modules, read its database or remote configuration, invoke Git or SSH, parse
provider transcripts, or construct provider/tmux commands.

The current public command boundary is:

```text
swbctl fleet --json
swbctl fleet --refresh --json
swbctl prepare-open <session-key> --host <host-id> --request-id <uuid> --json
swbctl prepare-task <task-id> --host <host-id> --request-id <uuid> --json
swbctl prepare-task <task-id> --host <host-id> --create --project <project-id> --title <text> --checkout <checkout-id> --provider <provider> --request-id <uuid> --json
swbctl prepare-history --project <project-id> --host <host-id> --checkout <checkout-id> --request-id <uuid> --json
swbctl stop-session <session-key> --host <host-id> --json
swbctl select-surface <surface-id> --host <host-id> --client <tmux-client-id>
swbctl attach-surface <surface-id> --host <host-id>
swbctl tui --view projects [--project <project-id> | --add-project]
```

Fleet v1 contains individually validated Snapshot v2 documents. It is not a
multi-host Snapshot and does not weaken each owning host's authority.
PresentationPlan v2 and SessionAction v2 remain the structured action inputs.

## Process split

`SwitchboardLauncher.qml` owns the synchronous DMS launcher surface and three
asynchronous Quickshell `Process` objects. `switchboard-bridge`
validates Fleet v1 and emits frontend model v4. `switchboard-open` asks the
bridge for a host-qualified plan, performs local niri focus or Ghostty launch,
and delegates select/attach back to local core. Core decides whether those
commands execute locally or through bounded SSH.

`switchboard-projects` is the third process path. It focuses an existing
`com.agent_switchboard.projects` window or opens one Ghostty running the public
core project TUI. It waits without imposing a lifetime deadline, then invokes
the sibling bridge once with `--refresh` and returns that one Bridge v3 record
to QML. The wrapper is scoped to the manager window and leaves no daemon.

All three helpers use fixed argv and no shell. The Python runner drains stdout and
stderr concurrently, bounds bytes and time, starts a separate process group,
and kills and reaps descendants after timeout, overflow, read/selector
failure, or an unexpected exception.

## Model v4

The privacy-bounded frontend model contains:

- local and configured remote host display, reachability, staleness, and
  bounded failure state, but never SSH targets;
- projects merged by stable ProjectId, with one host-local route per available
  snapshot;
- host-qualified open and closed tasks, identified by `(HostId, TaskId)`;
- host-qualified unassigned Inbox sessions;
- source-authored provider/runtime/activity truth; and
- bounded warnings and honest truncation counts.

Project routes include only host-local default checkouts that core projected as
present. A task or Inbox row carries its owning HostId privately for actions.
The visible format stays compact:

```text
task title
project | optional remote host | optional worktree | state | age
```

Local and remote rows with the same TaskId remain distinct. Compatible
ProjectIds share one native DMS category. Inbox and Closed span all retained
hosts. Within a project category, a nonempty query emits Codex and Claude
creation rows for each eligible host; the host is named when more than one
route qualifies.

A separate static Projects category exists even without a valid model. With a
model, it shows one compact row for each project that has a local route;
remote-only projects cannot be edited from this host. Add Project and Manage
Projects remain explicit catalog actions rather than task rows. Selecting them
sends at most a ProjectId to the wrapper—never a path, repository URL, or
mutation payload.

Codex uses `material:terminal`, Claude uses `material:auto_awesome`, and a task
without a current session uses `material:task_alt`. Absolute checkout paths,
SSH targets, transcripts, prompts, provider argv, tmux locators, and private
Git administrative identity never cross the model boundary.

## Host-qualified actions

Every actionable row supplies its owning HostId to `switchboard-open`, and the
helper supplies it to every local `swbctl` action. A returned plan for another
host fails closed. Desktop application identity hashes both HostId and the
opaque surface token, so equal-looking remote and local surfaces cannot focus
one another.

A focus miss reuses the same request ID and asks core for an attach fallback.
New tasks also retain the same generated TaskId. This preserves core's atomic
reservation and duplicate-prevention semantics across local and remote hosts.
Offline retained rows remain inspectable; selecting one attempts an
owner-revalidated action and reports a bounded failure rather than treating
cache state as mutation authority.

Claude history and safe launch-owned stop remain context actions, not duplicate
search rows. Stop appears only for source-projected `canStop=true`, and core
independently revalidates ownership before acting.

## Cache and refresh

`getItems(query)` is synchronous and reads only the last-good model. It uses
`Qt.callLater` to schedule `fleet --json` or one coalesced
`fleet --refresh --json` run. A complete bridge success atomically replaces
the model and stores it under `last_good_model_v4_bridge3`. A new launcher
instance synchronously reloads and fully revalidates that bounded model before
its asynchronous retained read.

Failures retain the last-good fleet and add an explicit status row. Missing
observations, unavailable hosts, and stale snapshots remain explicit rather
than becoming activity guesses. One initial no-model failure receives one
delayed retry; the budget resets only after success or a settings change.

DMS 1.5 does not connect launcher `itemsChanged()` to live result-list
mutation. Persisted state makes normal shell starts and plugin reloads useful
immediately. On first install or after cache removal, completed rows appear
when the launcher is reopened or its query changes.

The project-manager wrapper makes catalog changes converge without a manual
picker refresh: after the TUI closes, a full bridge read is revalidated by QML
and saved through the same last-good cache path. `itemsChanged()` is still
emitted as a best effort, but persisted state—not that ignored signal—is the
reliable handoff to the next launcher instance.

`SwitchboardModelV4.js` is a new physical module because Qt may retain relative
JavaScript imports across a plugin reload. Reload-significant envelope and
cache validation also remains in the cache-busted launcher QML component.

The `switchboard-launcher` IPC target exposes only versions, idle/generation
state, aggregate task/Inbox counts, and a stable failure code. It never emits
the model, item text, paths, host IDs, or provider/session IDs.

## Non-goals

This adapter does not configure remote hosts, run SSH, or itself edit projects,
repositories, worktrees, or configuration. It opens core's project manager and
consumes the resulting public snapshot. It also does not infer provider liveness,
accept arbitrary working directories, expose tmux locators, add
non-niri/non-Ghostty presentation, own a chezmoi cutover, or become a rich
widget.

## Historical contracts

Phases 1 through 3C used Snapshot v1 and location/session rows. Phase 4D used
Snapshot v2, frontend model v3, bridge v2, and adapter `0.2.1`. Phase 5 keeps
Snapshot v2 single-host and advances the DMS boundary to Fleet v1, frontend
model v4, bridge/action v3, and adapter `0.3.0`.
