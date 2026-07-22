# DMS 0.5 Architecture

Date: 2026-07-22

Status: implemented for coordinated Phase 6E activation

## Boundary

DMS is presentation glue for core-authored durable views. `switchboard-bridge`
runs one configured local `swbctl` with fixed argv, validates NavigatorState v1,
and projects Entry Model v1. `SwitchboardLauncher.qml` synchronously renders the
last-good model and asynchronously refreshes it. `switchboard-open` asks core
for one PresentationDirective v1, focuses an exact niri window, or starts one
leased Ghostty attach.

Core owns navigation, revisions, project/frame choice, recovery actionability,
desktop leases, tmux, SSH, providers, and all persistent mutation. The adapter
does not read core configuration or storage and never receives a path, provider
command, tmux locator, session identity, or semantic prompt.

## Model and cache

The default category is Views. Projects is always present; Recovery appears
only when core reports open structural recovery. Rows copy core-authored title,
breadcrumb, activity, attention, transition/control, reachability, staleness,
and actionability. Search filters visible text and never creates entries.

The only state key is `last_good_switchboard_entry_model_v1`. Its envelope binds
adapter `0.5.0`, validation version 1, source generation, generation time, and a
fully validated Entry Model v1. A cold instance may display it but cannot select
an entry until a fresh NavigatorState read validates current provenance. No
0.4 cache key or transformation exists.

## Desktop single flight

The application ID is `com.agent_switchboard.view.v` plus a truncated SHA-256
over owner HostId and the directive's opaque desktop token. Zero exact niri
matches triggers the same-request `--no-focus-desktop` fallback; one focuses;
more than one fails `ambiguous_desktop_windows`. Ordinary terminal/tmux clients
never match. Only an `attach` directive launches fixed Ghostty argv ending in
`swbctl view attach` for the same host, view, and request.

## Artifact and activation

`scripts/build-plugin` emits a deterministic ZIP with an exact file allowlist
and per-file SHA-256 manifest. It contains no old bridge, model, project-manager,
Fleet, Snapshot, task, Inbox, history, close, reopen, stop, or provider route.
`scripts/install-plugin stage` validates and atomically extracts it into a
content-addressed private version directory without changing the active plugin.
`activate` atomically publishes only an installer-owned symlink and refuses a
foreign active path.

The old plugin must be disabled and its windows closed before staging reads.
A real DMS process restart, not plugin reload, supplies cold-start evidence.
DMS is enabled only after snap then local core commits and trusted hook install.
