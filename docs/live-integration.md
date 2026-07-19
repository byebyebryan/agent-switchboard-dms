# Live DMS integration

## Supported DMS paths

DMS 1.5.0 reads user plugins from
`${XDG_CONFIG_HOME:-$HOME/.config}/DankMaterialShell/plugins` and system
plugins from `/etc/xdg/quickshell/dms-plugins`. `dms plugins install` accepts a
registry plugin ID, not a local checkout, so development uses a guarded local
symlink plus supported shell IPC.

The component harness and the real-shell workflow prove different boundaries:

- `scripts/live-integration` exercises launcher and settings QML with the
  installed DMS imports. It copies an existing Agent Switchboard state tree to
  a private temporary `XDG_STATE_HOME`; full refreshes cannot reconcile the
  user's registry. Its stdout contains summary counts, booleans, stable failure
  codes, and generations only.
- The `dms ipc call` workflow proves shell discovery, enablement, reload, and
  subprocess triggering. DMS 1.5.0 has no launcher-result query IPC, so it
  cannot return rendered launcher rows for an assertion.

## Reproducible real-shell runbook

This workflow temporarily changes DMS plugin settings, restarts DMS, and runs a
real Agent Switchboard reconciliation. Run the repository-owned verifier only
on a development shell where that disruption is acceptable:

```sh
./scripts/live-shell-integration \
  --swbctl /path/to/swbctl \
  --confirm-disruptive
```

The verifier requires the config directory, plugin directory, settings files,
and every relevant path component to be current-user-owned and nonsymlinked.
It refuses a preexisting Switchboard link or settings key and any normalized
baseline row whose plugin ID is `switchboard`, including a system plugin.
Before its first temporary directory it initializes rollback state and
installs EXIT, HUP, INT, and TERM cleanup. It then creates mode-0700 backup and
evidence directories, copies `plugin_settings.json` with mode, ownership, and
timestamps, records both DMS
configuration hashes and `mode:uid:gid` values, captures a nonempty normalized
plugin list, and records a cursor from the verified user journal unit:

```sh
journalctl --user -u dms.service -n 0 --show-cursor --no-pager
```

It then installs EXIT, HUP, INT, and TERM rollback traps. Signal exits retain
their conventional status; rollback ignores further signals while it disables
Switchboard, removes the guarded link, rescans, restores the backup through a
same-directory temporary file and atomic rename, restarts DMS, and verifies the
result. A failed rollback retains the private backup and prints its location
for emergency recovery. A successful rollback deletes both private trees.

The guarded lifecycle uses these supported IPC calls; disable and rescan also
run from the rollback path:

```sh
dms ipc call plugin-scan scan
dms ipc call plugins enable switchboard
dms ipc call plugins reload switchboard
dms ipc call plugins disable switchboard
```

The shell portion uses the supported DMS 1.5 one-argument launcher call:

```sh
dms ipc call launcher openQuery 'sb:switchboard'
```

There is no launcher-result query IPC. Instead, a bounded `/proc` sampler runs
in an owned process group and recognizes exact argument arrays while emitting
marker names only. Signal handling is deferred across sampler publication and
wait/reap bookkeeping, so rollback never targets a reaped or reused PID. The
retained gate
requires both the bridge with its configured `swbctl` and exact
`snapshot --json`; after the five-second model becomes stale, the refresh gate
requires the bridge `--refresh` form and exact
`snapshot --reconcile full --json`. Raw process arguments and snapshot output
are not saved.

Logs come exclusively from the verified `dms.service` user journal, bounded by
the cursor captured before installation:

```sh
journalctl --user -u dms.service \
  --after-cursor "$JOURNAL_CURSOR" \
  --no-pager -o short-iso-precise
```

Journal capture failure is fatal. The capture must be nonempty, contain a DMS
service record, and contain no Switchboard bad-manifest, component,
instantiation, or load errors, including the installed DMS form
`Error loading plugin: switchboard`. The verifier also requires the during-test
plugin list to equal the original list plus loaded Switchboard and the final
list to exactly equal the normalized baseline. Final configuration hashes and
modes must match, with no Switchboard link or settings key.

The full-refresh sample changes the user's Agent Switchboard runtime registry
by design, though it does not change the core source checkout. Prefer the
component harness below when DMS shell discovery itself is not under test.

## Privacy-safe component harness

Run the installed-import harness with an executable and, optionally, an
explicit state source:

```sh
./scripts/live-integration \
  --swbctl /path/to/swbctl \
  --state-source "${XDG_STATE_HOME:-$HOME/.local/state}/agent-switchboard"
```

The source state must be owned by the current user, contain no symlinks, and
contain at least one session. The harness copies it beneath a mode-0700
temporary directory, gives Quickshell private config, state, cache, and data
roots, and starts Quickshell in a new process group. Exit, HUP, INT, and TERM
cleanup terminate that whole group, escalate to KILL when needed, reap the
leader, and remove the temporary tree. No debug-retention option is provided.

It requires an active display and verifies all of the following without
printing raw model data:

- a nonempty retained model and exactly one internal match for a real session
  identifier;
- positive settings height and focus;
- a full refresh with an advanced generation and source timestamp;
- exact last-good model retention through `executable_not_found` and
  `bridge_start_failed`, followed by generation-advancing recovery.

A successful component run ends with `LIVE_INTEGRATION_OK`. The 2026-07-16
evidence exercise used DMS 1.5.0, Quickshell 0.3.0, and Qt 6.11.1 and also
included the separate real-shell lifecycle. Its selected, sanitized historical
shell transcript was:

```text
unloaded launcher
PLUGIN_ENABLE_SUCCESS: switchboard
loaded launcher
PLUGIN_RELOAD_SUCCESS: switchboard
LAUNCHER_OPEN_QUERY_SUCCESS
SETTINGS_OPEN_SUCCESS: plugins
$REPO/switchboard-bridge --swbctl $CORE_SWBCTL
$CORE_SWBCTL snapshot --json
$REPO/switchboard-bridge --swbctl $CORE_SWBCTL --refresh
$CORE_SWBCTL snapshot --reconcile full --json
PLUGIN_DISABLE_SUCCESS: switchboard
```

The rollback-enforced `live-shell-integration` verifier was added after that
exercise and was syntax- and contract-tested without rerunning its disruptive
workflow against the live shell.

The original plugin configuration was restored byte-for-byte, the development
symlink was absent, and the shell again listed only the preexisting plugins.
Rapid back-to-back reloads did produce unrelated Qt invalid-context warnings;
the evidence claims no Switchboard component/load errors, not a globally silent
shell log.

## Phase 3A local action evidence

The 2026-07-16 action exercise used the installed development symlink and the
public core commands without restarting DMS or tmux:

1. Core full reconciliation identified the current Codex runtime as live and
   attached to the current tmux pane, with no managed surface yet.
2. `switchboard-open` prepared that canonical session key. Core atomically
   adopted the pane; the helper matched its pre-Switchboard title using the
   exact tmux workspace prefix and short host suffix, then focused the existing
   Ghostty window.
3. niri contained seven windows before and after. No new Ghostty window or
   provider runtime was created, and the tmux server PID was unchanged.
4. `dms ipc call plugins reload switchboard` and
   `dms ipc call launcher openQuery 'sb:switchboard'` both succeeded. The DMS
   service PID and tmux server PID remained unchanged. The separate legacy
   `agentSessions` plugin path remained present and untouched.

Codex hook installation completed through
`swbctl hooks install --provider codex`, and all five Agent Switchboard handlers
were subsequently reviewed and trusted through Codex `/hooks`. `swbctl doctor`
then reported healthy on Codex 0.144.4.

The trusted parked-session exercise opened one retained resumable session
through `switchboard-open`. DMS launched one managed Ghostty window attached to
one waiting tmux surface, and core started exactly one
`codex resume <session-uuid>` process. The existing tmux server PID was
unchanged. That Codex resume did not leave a retained `SessionStart` event, so
core required exact durable session ID, process-birth, and full launch-owned
tmux locator evidence before atomically confirming the surface binding.

Opening the same session again returned a `focused` action for the existing
surface. The managed Ghostty window count and matching Codex process count both
remained one. This completes the Phase 3A live DMS acceptance without claiming
that every Codex resume emits `SessionStart`.

## Phase 3C known-Claude action evidence

The 2026-07-18 exercise used the installed core wheel, the development DMS
symlink, an isolated Switchboard registry/tmux server, and a controlled native
Claude transcript. A structured `UserPromptSubmit` blocker plus an invalid
loopback API endpoint created that UUID with zero turns and provider-reported
`total_cost_usd=0`.

Core prepared one waiting Claude surface, waited for a real tmux client, then
executed the installed Claude binary with the exact `--resume <uuid>` suffix.
The process inherited `CLAUDE_CODE_DISABLE_AGENT_VIEW=1`, and the Claude
`SessionStart` hook atomically produced one bound launch and one confirmed live
surface. No Agent View/background daemon was present. Reopening the same stable
session key reused the only active surface; the registry retained one launch,
one surface, and one Claude pane.

`switchboard-bridge --refresh` emitted private model v2 with one live Claude
row, an available Claude capability, and no warnings. `switchboard-open` then
returned a successful `launched` action, started its transient Ghostty scope,
and attached a client to that same core surface without creating another Claude
runtime.

This control shell could connect to the systemd-published niri socket, but that
socket reported zero windows before and after the Ghostty attach. The exercise
therefore proves the provider, bridge, desktop-helper launch, and same-surface
dedup paths; it does not claim live niri focus or same-window dedup. That final
compositor check remains open. The test-owned Claude process exited cleanly,
full reconciliation returned the session to stopped, and the isolated state was
removed.

## Phase 3C new-Claude action evidence

The 2026-07-18 new-session exercise used the installed core wheel, the retained
development bridge, isolated Switchboard state, and a dedicated tmux server.
Core reserved one unbound Claude surface and did not execute the provider until
a real client attached. The resulting process ran plain `claude`, inherited
`CLAUDE_CODE_DISABLE_AGENT_VIEW=1`, and emitted a provider-assigned UUID through
the installed `SessionStart` hook.

The hook bound that UUID to the expected launch, project, location, and surface
with confirmed live runtime evidence. Reopening the canonical session key
returned a focus plan for the same surface, synchronized the UUID into tmux
metadata, and retained the same test-owned process. The bridge then emitted
model v2 with one live confirmed Claude row, explicit Codex and Claude launch
targets, and no warnings.

No prompt was submitted and no model turn was requested. `/exit` stopped only
the test-owned Claude process; full reconciliation reported the UUID stopped and
resumable, the dedicated tmux server exited, and the empty test transcript was
moved to the desktop trash. The pre-existing active Claude session remained
alive throughout. This exercise did not launch Ghostty, so live niri focus and
same-window dedup remain the compositor acceptance item described above.

## Phase 3C history and stop evidence

The 2026-07-18 final-increment exercise refreshed the installed core tool from
the completed checkout and used the development DMS symlink, an isolated
registry, and a dedicated tmux server. It first confirmed that the bridge
projected the controlled confirmed Claude runtime with `canStop=true`, both
provider launch targets, and no warnings. The public stop action sent orderly
interactive `/exit`, retired the exact surface, and removed only the
test-owned process; the unrelated pre-existing Claude session remained alive.

The history action then created one unbound attach-before-start surface and
opened Claude Code 2.1.214's native `claude --resume` picker with Agent View
disabled. Selecting the most recent test-owned conversation rebound its exact
UUID to the new surface through `SessionStart`, after which the same safe stop
path returned it to stopped and resumable. No picker rows, transcript content,
provider argv, cwd, or tmux locator entered the DMS model.

A second history action was cancelled with the picker's native Escape control.
Complete reconciliation retired the vanished surface and recorded the unbound
history launch as failed with `surface_terminated`; it did not create a
session. No prompt was submitted and no model turn was requested. The isolated
registry and tmux server were removed, and only the generated test transcript
was moved to the desktop trash. This completes the provider, bridge, and DMS
action contract while leaving the earlier live niri focus/same-window
observation gap explicitly unclaimed.

## Phase 3C compositor closeout evidence

A follow-up 2026-07-18 exercise used the active DMS service's niri and Wayland
environment with an isolated Switchboard state tree and a dedicated tmux
socket. The test wrapper explicitly removed the control shell's outer `TMUX`
value. The installed helper launched one managed Ghostty window whose exact
application ID resolved to one niri window.

Reopening the controlled Claude session through `switchboard-open` returned a
`focused` action for the existing surface. Its niri window ID was unchanged and
the matching managed-window count remained one, completing the live focus and
same-window dedup check that the earlier harness could not observe. Claude
started only after the real terminal client attached; no prompt was submitted
and no model turn was requested.

The public stop action removed only the controlled Claude runtime and managed
window. The unrelated pre-existing Claude session remained alive, no
test-owned process or surface remained, the isolated state was removed, and
the empty test transcript was moved to the desktop trash. Phase 3C compositor
acceptance is therefore complete without adding niri, Ghostty, provider argv,
or tmux identity to the DMS model.

## Qt 6 and automation boundary

Use the Qt 6 tools explicitly on the evidence machine:

```sh
/usr/lib/qt6/bin/qmlformat SwitchboardLauncher.qml SwitchboardSettings.qml tests/live/Shell.qml
/usr/lib/qt6/bin/qmllint -I /usr/share/quickshell/dms \
  SwitchboardLauncher.qml SwitchboardSettings.qml tests/live/Shell.qml
```

`scripts/check` byte-compares all three files with Qt 6 `qmlformat` when that
binary exists. Qt 6 `qmllint` exits successfully but reports unresolved
`qs.Common`, `qs.Modules.Plugins`, and `qs.Widgets` imports plus dynamic DMS
types; Quickshell resolves those specially from its configuration root. It is
therefore useful diagnostics, not a clean lint gate. The active-display harness
is the runtime validation. CI does not claim headless QML, `qmltestrunner`, or
live-shell coverage.
