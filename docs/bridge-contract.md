# Bridge and Desktop Contract v1

## Bridge

The only reads are:

```text
[SWBCTL, "state", "navigator", "--json"]
[SWBCTL, "state", "navigator", "--refresh", "--json"]
```

The successful output is one canonical record plus LF:

```json
{"bridgeVersion":1,"model":{"modelVersion":1,"sourceNavigatorVersion":1},"ok":true}
```

NavigatorState must use schema/protocol/navigator version 1, canonical UUIDs and
ordering, one exact local host/generation, valid owner references, bounded text
and collections, and no sensitive future field. Failures are structured,
bounded, contain no source stderr/stdout, and exit 1. All subprocesses use fixed
argv, concurrent bounded drains, a process group, timeout, kill, and reap.

## Desktop action

One activation uses exactly one of `--view`, `--project`, or `--recovery`. The
helper creates a request UUID and invokes the corresponding core v1 route with
`--can-focus-desktop --can-launch-terminal --json`. A focus miss repeats the
identical host/target/request with `--no-focus-desktop`.

`PresentationDirective v1` must match the requested host and request. `focus`
and `attach` require a view revision and opaque desktop token; only `attach`
has an expiry. `blocked` contains only a bounded error. A fallback must be an
attach for the same view and token.

Ghostty starts only:

```text
[SYSTEMD_RUN, "--user", "--scope", "--collect", "--quiet", "--",
 GHOSTTY, "--class=" + OPAQUE_APP_ID, "-e",
 SWBCTL, "view", "attach", "--host", HOST,
 "--view", VIEW, "--request-id", REQUEST]
```

The desktop result is action v1 with `focused` or `launched`. There is no switch,
task, session, history, close, reopen, stop, checkout, provider, SSH, or tmux
desktop action.
