# DMS 0.5 Live Acceptance

## Isolated component process

With a staged core generation and installed DMS QML imports:

```sh
scripts/live-integration \
  --swbctl /path/to/installed-0.3/bin/swbctl \
  --config-root /path/to/isolated/config/agent-switchboard \
  --state-root /path/to/isolated/state/agent-switchboard
```

The harness starts a fresh Quickshell process, validates Views/Projects/Recovery
categories, visible-text query, cache round trip, read-only cached provenance,
fresh refresh, and last-good retention. It does not activate an item.

## Installed cold start

Phase 6E acceptance must additionally:

1. disable and close the old plugin;
2. stage the exact 0.5 artifact without changing the active path;
3. stop the DMS user service and prove its old process is gone;
4. activate the versioned plugin and start a new DMS process;
5. record the new system boot ID, service invocation ID/MainPID/start ticks, and
   model/cache hashes as the cold-start identity;
6. prove the old cache key is ignored and the v1 key warms a second instance;
7. while core is staged, render state and prove every desktop mutation is
   blocked rather than converted to launch;
8. after both core commits, exercise one focus, one focus-miss attach, duplicate
   window refusal, Project navigation, Recovery actionability, offline retained
   rows, and remote owner presentation.

Plugin reload is never accepted as cold-start evidence. The one-shot executor
keeps DMS disabled between the snap-first and local-second core commits.
