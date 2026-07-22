# Agent Switchboard for DankMaterialShell

DMS adapter `0.5.0` is a small desktop entry surface for Agent Switchboard
core `0.3.0`. It renders durable Views, Project navigation, and structural
Recovery from `NavigatorState v1`. Core remains the sole authority for frames,
sessions, tmux, SSH, providers, and mutation.

The adapter invokes only:

```text
swbctl state navigator [--refresh] --json
swbctl view open --host HOST (--view VIEW | --project PROJECT) ... --json
swbctl view recover --host HOST --recovery RECOVERY ... --json
swbctl view attach --host HOST --view VIEW --request-id REQUEST
```

It stores one fully validated last-good cache under
`last_good_switchboard_entry_model_v1`. Cached rows are read-only until a fresh
read validates the source generation. A focus miss repeats the same semantic
request with desktop focus disabled; only a core-issued attach lease may start
Ghostty. Desktop identity is an opaque hash of owner HostId and desktop token.

Development checks:

```sh
scripts/check
```

Build a deterministic install artifact:

```sh
SOURCE_DATE_EPOCH=1784073600 scripts/build-plugin --output /tmp/switchboard-0.5.0.zip
scripts/install-plugin --plugin-dir "$HOME/.config/DankMaterialShell/plugins" \
  stage --archive /tmp/switchboard-0.5.0.zip
```

Staging never changes the active plugin. Coordinated Phase 6E activation uses
the returned versioned directory only after both core hosts commit. See
[`docs/view-entry-plan.md`](docs/view-entry-plan.md) and
[`docs/architecture.md`](docs/architecture.md).
