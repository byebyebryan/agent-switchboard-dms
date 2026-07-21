# Snapshot v2 fixtures

`snapshot-v2.json` is synthetic test data adapted from the public
`byebyebryan/agent-switchboard` fixture at:

`tests/fixtures/protocol/v2/snapshot.json`

The reviewed core contract is Agent Switchboard commit `803f0f8`. Runtime code
still consumes only the public executable/JSON boundary and does not import
that repository.

It adds a closed task and an unassigned Claude session so the DMS model can
exercise open-task, Closed, Inbox, provider-badge/state-icon, and safe-stop
behavior. It is not a capture of a live machine or user session.

SHA-256:
`d70748e05eab95327f5f426266cf433223834507efaebcb4a9cb203d0c320eff`

`snapshot-v1-mixed.json` is retained only as an incompatible-input fixture. A
0.2.0 bridge must reject it rather than reinterpret the old location model.
