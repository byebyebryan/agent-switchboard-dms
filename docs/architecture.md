# Architecture

## Ownership boundary

The plugin is a strict frontend for a user-configured `swbctl`. It must not
import internal Agent Switchboard modules, read its database, invoke Git, parse
provider transcripts, or gain provider/tmux ownership. The public boundary is:

```text
swbctl snapshot --json
swbctl snapshot --reconcile full --json
swbctl prepare-open <session-key> --request-id <uuid> --json
swbctl prepare-task <task-id> --request-id <uuid> --json
swbctl prepare-task <task-id> --create --project <project-id> --title <text> --checkout <checkout-id> --provider <provider> --request-id <uuid> --json
swbctl prepare-history --project <project-id> --checkout <checkout-id> --request-id <uuid> --json
swbctl stop-session <session-key> --json
swbctl select-surface <surface-id> --client <tmux-client-id>
swbctl attach-surface <surface-id>
```

Snapshot v2 JSON, PresentationPlan v2 JSON, and SessionAction v2 JSON are the
only structured core inputs. The bridge rejects v1 rather than translating
locations into repositories, checkouts, or tasks.

## Process split

`SwitchboardLauncher.qml` owns the DMS launcher interface and two persistent
Quickshell `Process` objects. `switchboard-bridge` performs bounded source
validation and emits frontend model v3. `switchboard-open` asks the bridge for
one validated plan and performs only local niri/Ghostty presentation. Core
still owns tmux locators and attachment.

Both helpers use fixed argv with no shell. The Python process runner drains
stdout and stderr concurrently, imposes byte/time limits, launches a new
process group, and kills/reaps descendants on timeout, overflow, selector
failure, read failure, or unexpected exceptions.

## Model v3

The frontend model contains only bounded display/routing data:

- declared projects and their primary/default checkout route;
- open and closed task rows with stable task/project/checkout IDs;
- unassigned Inbox sessions;
- source-authored provider/runtime/activity truth;
- provider capabilities, bounded warnings, and truncation counts.

Task rows contain stable IDs and use the task title as the primary line. The
second line is project, optional nondefault worktree branch/name, state, and
age. Absolute checkout paths and Git administrative identity never cross into
the model. Codex uses `material:terminal`, Claude uses
`material:auto_awesome`, and a task without a current session uses
`material:task_alt`.

The native plugin category contract exposes All tasks, one category per
project, Inbox, and Closed. All tasks includes open tasks plus one
non-actionable Inbox summary. The Inbox category shows exact provider sessions.
Closed shows closed tasks.

Inside one project category, an empty query produces no creation action. A
nonempty query produces bounded Codex and Claude creation rows. Selection
generates a task UUID and request UUID in `switchboard-open`, then invokes
atomic `prepare-task --create` in the project's default checkout.

Safe Claude stop and Claude native history are DMS context actions. They are
not duplicate launcher rows. Stop appears only when the source model projects
`canStop=true`; core revalidates ownership before acting.

## Cache and refresh

`getItems(query)` is synchronous and reads only the last-good model. It uses
`Qt.callLater` to schedule a retained read or one coalesced refresh. A valid
complete response atomically replaces the cache. Failure retains the last-good
snapshot and adds an explicit status row. Missing observations and stale data
remain source truth; an absent provider record becomes neutral Codex or Claude
capability rather than a failure.

DMS 1.5.0 does not connect that signal to live launcher-result mutation, so
new rows become visible when the launcher is reopened or the query changes.
Timeout handling uses `Process.signal(15)` and the helper's process-group guard.

## Non-goals

This adapter does not add SSH, provider hooks or liveness inference,
arbitrary working-directory launch, project-catalog editing, a direct tmux
locator, non-niri/non-Ghostty adapters, a chezmoi cutover, or a rich widget.
Repositories/worktrees are discovered by core; the plugin never mutates them.

## Historical phases

Phases 1 through 3C used Snapshot v1, project locations, session rows, and
separate launch/history/stop rows. Those contracts are preserved in Git
history and the implementation-plan chronology, but Phase 4D supersedes them
for the current 0.2.0 runtime.
