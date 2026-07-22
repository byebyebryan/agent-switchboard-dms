# DMS 0.5 Delivery Plan

The clean-break implementation is complete on the Phase 6E branch:

1. NavigatorState v1 validation and Entry Model v1 projection.
2. Fixed retained/refresh bridge with bounded process lifecycle.
3. PresentationDirective v1 routing, exact niri identity, same-request focus
   fallback, ambiguity refusal, and leased Ghostty attach.
4. Views/Projects/Recovery QML, generation-provenance cache, and read-only cold
   cache behavior.
5. Deterministic artifact, content manifest, inactive versioned staging, and
   atomic installer-owned activation.
6. Unit, JavaScript, static QML, process, artifact, and clean-break audits.

Remaining Phase 6E work is operational acceptance, not another compatibility
layer: run the isolated QML harness, perform a real DMS cold restart, collect
two-host evidence, commit snap then local, activate the plugin, and exercise
the installed desktop paths. Phase 6F recursive frames remains core work.
