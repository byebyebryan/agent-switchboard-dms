"use strict"

const assert = require("assert")
const fs = require("fs")
const path = require("path")
const vm = require("vm")

const root = path.resolve(__dirname, "..")
const source = fs
    .readFileSync(path.join(root, "SwitchboardModel.js"), "utf8")
    .replace(/^\.pragma library\s*$/m, "")
const modelApi = {}
vm.createContext(modelApi)
vm.runInContext(source, modelApi, { filename: "SwitchboardModel.js" })

function session(overrides = {}) {
    return Object.assign(
        {
            sessionKey: "11111111-1111-4111-8111-111111111111:codex:55555555-5555-4555-8555-555555555555",
            hostId: "11111111-1111-4111-8111-111111111111",
            provider: "codex",
            providerSessionId: "55555555-5555-4555-8555-555555555555",
            projectId: "22222222-2222-4222-8222-222222222222",
            projectName: "routing console",
            locationId: "44444444-4444-4444-8444-444444444444",
            locationName: "local checkout",
            name: "repair cache",
            purpose: null,
            cwd: "/work/agent-switchboard",
            firstObservedAt: 90000,
            lastObservedAt: 100000,
            createdAt: null,
            providerUpdatedAt: null,
            lastActivityAt: null,
            stateObservedAt: null,
            wrappedAt: null,
            recencyAt: 100000,
            metadataSource: "provider",
            runtimePresence: "live",
            resumability: "resumable",
            activity: "working",
            activityReason: "unknown",
            attachment: "detached",
            stateConfidence: "confirmed",
            pinned: false
        },
        overrides
    )
}

function launchTarget(overrides = {}) {
    return Object.assign(
        {
            projectId: "22222222-2222-4222-8222-222222222222",
            projectName: "routing console",
            locationId: "44444444-4444-4444-8444-444444444444",
            locationName: "local checkout",
            provider: "codex",
            isDefault: true
        },
        overrides
    )
}

function model(overrides = {}) {
    return Object.assign(
        {
            modelVersion: 2,
            sourceSchemaVersion: 1,
            sourceProtocolVersion: 1,
            generatedAt: 100000,
            host: {
                hostId: "11111111-1111-4111-8111-111111111111",
                displayName: "snap",
                futureHostField: true
            },
            sessions: [session()],
            launchTargets: [launchTarget()],
            capabilities: [
                {
                    provider: "codex",
                    status: "available",
                    available: true,
                    features: [],
                    degradedReasons: []
                },
                {
                    provider: "claude",
                    status: "neutral",
                    available: null,
                    features: [],
                    degradedReasons: []
                }
            ],
            warnings: [],
            diagnosticTruncation: {},
            truncation: {},
            futureModelField: { ignored: true }
        },
        overrides
    )
}

function state(overrides = {}) {
    return Object.assign(
        {
            now: 101000,
            loading: false,
            stale: false,
            failure: null
        },
        overrides
    )
}

function sessionItems(items) {
    return items.filter(item => item._switchboardKind === "session")
}

function newItems(items) {
    return items.filter(item => item._switchboardKind === "new")
}

{
    const envelope = {
        bridgeVersion: 1,
        ok: true,
        model: model(),
        futureBridgeField: "ignored"
    }
    const parsed = modelApi.parseBridgeResponse(JSON.stringify(envelope))
    assert.strictEqual(parsed.ok, true)
    assert.strictEqual(parsed.model.futureModelField.ignored, true)
}

{
    const older = session({
        sessionKey: "11111111-1111-4111-8111-111111111111:codex:66666666-6666-4666-8666-666666666666",
        providerSessionId: "66666666-6666-4666-8666-666666666666",
        name: "older",
        recencyAt: 90000
    })
    const current = model({ sessions: [older, session()] })
    const first = sessionItems(modelApi.launcherItems(current, "", state()))
    const second = sessionItems(modelApi.launcherItems(current, "", state({ now: 200000 })))
    assert.strictEqual(
        JSON.stringify(first.map(item => item.name)),
        JSON.stringify(["repair cache", "older"])
    )
    assert.strictEqual(
        JSON.stringify(first.map(item => item.id)),
        JSON.stringify(second.map(item => item.id))
    )
    assert.strictEqual(first[0].id.startsWith("switchboard:session:"), true)
    assert.strictEqual(Object.prototype.hasOwnProperty.call(first[0], "action"), false)
    assert.strictEqual(first[0]._windowHost, "snap")
}

{
    const secondLocation = launchTarget({
        locationId: "77777777-7777-4777-8777-777777777777",
        locationName: "worktree",
        isDefault: false
    })
    const items = newItems(modelApi.launcherItems(
        model({ launchTargets: [launchTarget(), secondLocation] }),
        "",
        state()
    ))
    assert.strictEqual(items.length, 2)
    assert.strictEqual(items[0].name, "New Codex — routing console")
    assert.strictEqual(items[1].name, "New Codex — routing console — worktree")
    assert.strictEqual(items[0]._projectId, launchTarget().projectId)
    assert.strictEqual(items[1]._locationId, secondLocation.locationId)
    assert.strictEqual(Object.prototype.hasOwnProperty.call(items[0], "_sessionKey"), false)
}

{
    const success = modelApi.parseActionResponse(JSON.stringify({
        actionVersion: 1,
        ok: true,
        action: { kind: "focused", surfaceId: "33333333-3333-4333-8333-333333333333" }
    }))
    const failure = modelApi.parseActionResponse(JSON.stringify({
        actionVersion: 1,
        ok: false,
        error: { code: "unmanaged_surface", message: "Cannot focus this runtime.", retryable: false }
    }))
    assert.strictEqual(success.ok, true)
    assert.strictEqual(success.action.kind, "focused")
    assert.strictEqual(failure.ok, false)
    assert.strictEqual(failure.error.code, "unmanaged_surface")
}

for (const badAction of [
    "not json",
    JSON.stringify({ actionVersion: 2, ok: true, action: {} }),
    JSON.stringify({ actionVersion: 1, ok: true, action: { kind: "unknown", surfaceId: "surface" } }),
    JSON.stringify({ actionVersion: 1, ok: false, error: {} })
]) {
    assert.strictEqual(modelApi.parseActionResponse(badAction).ok, false)
}

for (const query of [
    "repair cache",
    "/work/agent-switchboard",
    "routing console",
    "local checkout",
    "11111111-1111-4111-8111-111111111111:codex:55555555-5555-4555-8555-555555555555",
    "codex",
    "snap"
]) {
    const items = sessionItems(modelApi.launcherItems(model(), query, state()))
    assert.strictEqual(items.length, 1, `query did not match: ${query}`)
}

{
    const claude = session({
        sessionKey: "11111111-1111-4111-8111-111111111111:claude:77777777-7777-4777-8777-777777777777",
        provider: "claude",
        providerSessionId: "77777777-7777-4777-8777-777777777777",
        name: null
    })
    const items = sessionItems(modelApi.launcherItems(model({ sessions: [claude] }), "claude", state()))
    assert.strictEqual(items.length, 1)
    assert.strictEqual(items[0].name, "routing console")
    assert.strictEqual(items[0]._sessionKey, claude.sessionKey)
    assert.match(items[0].comment, /Claude/)
}

{
    const value = session()
    const identityOnly = modelApi._searchText(value, {
        displayName: "different host",
        hostId: "99999999-9999-4999-8999-999999999999"
    })
    assert.strictEqual(identityOnly.includes(value.sessionKey.toLowerCase()), true)

    const withoutSessionIdentity = Object.assign({}, value, {
        sessionKey: null,
        providerSessionId: null
    })
    const hostOnly = modelApi._searchText(withoutSessionIdentity, model().host)
    assert.strictEqual(hostOnly.includes(model().host.hostId.toLowerCase()), true)

    assert.strictEqual(source.includes("toLocaleLowerCase"), false)
    const turkishIndependent = model({ sessions: [session({ name: "ISTANBUL" })] })
    assert.strictEqual(
        sessionItems(modelApi.launcherItems(turkishIndependent, "istanbul", state())).length,
        1
    )
}

{
    const activeFull = modelApi.planRunRequest(
        {
            active: true,
            runWasRefresh: true,
            settingsGeneration: 3,
            runSettingsGeneration: 3,
            pendingRefresh: false,
            startScheduled: false
        },
        true
    )
    assert.strictEqual(activeFull.queueRun, false)
    assert.strictEqual(activeFull.pendingRefresh, false)

    const laterRetained = modelApi.planRunRequest(
        {
            active: false,
            runWasRefresh: false,
            settingsGeneration: 3,
            runSettingsGeneration: 3,
            pendingRefresh: activeFull.pendingRefresh,
            startScheduled: false
        },
        false
    )
    assert.strictEqual(laterRetained.shouldSchedule, true)
    assert.strictEqual(laterRetained.pendingRefresh, false)

    const changedSettings = modelApi.planRunRequest(
        {
            active: true,
            runWasRefresh: true,
            settingsGeneration: 4,
            runSettingsGeneration: 3,
            pendingRefresh: false,
            startScheduled: false
        },
        false
    )
    assert.strictEqual(changedSettings.queueRun, true)
    assert.strictEqual(changedSettings.queueRefresh, true)
}

{
    const base = {
        runActive: true,
        running: false,
        observedRunGeneration: 7,
        runGeneration: 7,
        settingsGeneration: 3,
        runSettingsGeneration: 3,
        exitFinished: false,
        runExpired: false
    }
    assert.strictEqual(modelApi.stoppedRunDisposition(base, false), "start_failed")
    assert.strictEqual(
        modelApi.stoppedRunDisposition(Object.assign({}, base, { exitFinished: true }), false),
        "wait"
    )
    assert.strictEqual(
        modelApi.stoppedRunDisposition(Object.assign({}, base, { exitFinished: true }), true),
        "incomplete"
    )
    assert.strictEqual(
        modelApi.stoppedRunDisposition(Object.assign({}, base, { observedRunGeneration: 6 }), true),
        "none"
    )
    assert.strictEqual(
        modelApi.stoppedRunDisposition(Object.assign({}, base, { settingsGeneration: 4 }), false),
        "stale"
    )
    assert.strictEqual(
        modelApi.stoppedRunDisposition(Object.assign({}, base, { runExpired: true }), true),
        "expired"
    )
}

{
    const previous = model({ generatedAt: 50000 })
    let lastGood = previous
    const oldSuccess = modelApi.parseBridgeResponse(JSON.stringify({
        bridgeVersion: 1,
        ok: true,
        model: model({ generatedAt: 200000 })
    }))
    if (modelApi.shouldAcceptRunResult(1, 2, false, 0, oldSuccess.ok))
        lastGood = oldSuccess.model

    const currentFailure = modelApi.parseBridgeResponse(JSON.stringify({
        bridgeVersion: 1,
        ok: false,
        error: { code: "process_spawn_failed", message: "Current executable failed.", retryable: true }
    }))
    if (modelApi.shouldAcceptRunResult(2, 2, false, 1, currentFailure.ok))
        lastGood = currentFailure.model

    assert.strictEqual(lastGood, previous)
    assert.strictEqual(oldSuccess.ok, true)
    assert.strictEqual(currentFailure.ok, false)
}

{
    assert.strictEqual(modelApi.MAX_EXECUTABLE_LENGTH, 4096)
    assert.strictEqual(modelApi.boundedExecutable("x".repeat(5000)).length, 4096)
    assert.strictEqual(modelApi.boundedExecutable("/path with spaces"), "/path with spaces")
    assert.strictEqual(modelApi.boundedExecutable(""), "swbctl")
    assert.strictEqual(modelApi.boundedExecutable("", "ghostty"), "ghostty")
}

{
    const items = modelApi.launcherItems(model(), "missing", state())
    assert.strictEqual(sessionItems(items).length, 0)
    assert.strictEqual(items.some(item => item.id === "switchboard:status:no-match"), true)
}

{
    const items = modelApi.launcherItems(model({ sessions: [], launchTargets: [] }), "", state())
    assert.strictEqual(items.some(item => item.id === "switchboard:status:empty"), true)
}

{
    const degraded = model({
        capabilities: [
            {
                provider: "codex",
                status: "degraded",
                available: false,
                features: [],
                degradedReasons: [{ code: "provider_probe_failed", retryable: true }]
            },
            {
                provider: "claude",
                status: "neutral",
                available: null,
                features: [],
                degradedReasons: []
            }
        ]
    })
    const items = modelApi.launcherItems(degraded, "", state())
    assert.strictEqual(items[0].id, "switchboard:status:capability-degraded")
    assert.strictEqual(sessionItems(items).length, 1)
}

{
    const neutral = model({
        sessions: [session({
            runtimePresence: "unknown",
            resumability: "unknown",
            activity: "unknown",
            attachment: "unknown"
        })],
        capabilities: [
            {
                provider: "codex",
                status: "neutral",
                available: null,
                features: [],
                degradedReasons: []
            },
            {
                provider: "claude",
                status: "neutral",
                available: null,
                features: [],
                degradedReasons: []
            }
        ]
    })
    const items = modelApi.launcherItems(neutral, "", state())
    assert.strictEqual(items[0].id, "switchboard:status:capability-neutral")
    assert.match(sessionItems(items)[0].comment, /state unknown/)
}

{
    const degraded = model({
        capabilities: [
            {
                provider: "codex",
                status: "available",
                available: true,
                features: [],
                degradedReasons: []
            },
            {
                provider: "claude",
                status: "degraded",
                available: false,
                features: [],
                degradedReasons: [{ code: "agent_view_enabled", retryable: false }]
            }
        ]
    })
    const items = modelApi.launcherItems(degraded, "", state())
    assert.strictEqual(items[0].id, "switchboard:status:claude-capability-degraded")
    assert.strictEqual(items[0].name, "Claude data is degraded")
}

{
    const staleState = state({ now: 200000, stale: true })
    assert.strictEqual(modelApi.isStale(model(), staleState.now, 15), true)
    const items = modelApi.launcherItems(model(), "", staleState)
    assert.strictEqual(items[0].id, "switchboard:status:stale")
    assert.strictEqual(sessionItems(items).length, 1)
}

{
    const failure = { code: "process_timeout", message: "Snapshot refresh timed out.", retryable: true }
    const retained = modelApi.launcherItems(model(), "", state({ failure }))
    assert.strictEqual(retained[0].id, "switchboard:status:degraded-refresh")
    assert.strictEqual(sessionItems(retained).length, 1)

    const unavailable = modelApi.launcherItems(null, "", state({ failure }))
    assert.strictEqual(unavailable[0].id, "switchboard:status:error")
}

{
    assert.strictEqual(
        modelApi.launcherItems(null, "", state({ loading: true }))[0].id,
        "switchboard:status:loading"
    )
    assert.strictEqual(
        modelApi.launcherItems(null, "", state())[0].id,
        "switchboard:status:initial"
    )
}

for (const badEnvelope of [
    "not json",
    JSON.stringify({ bridgeVersion: 2, ok: true, model: model() }),
    JSON.stringify({ bridgeVersion: 1, ok: true, model: model({ sessions: [{}] }) }),
    JSON.stringify({ bridgeVersion: 1, ok: true, model: model({ launchTargets: [{}] }) }),
    JSON.stringify({ bridgeVersion: 1, ok: true, model: model({ sessions: [session({ activity: "idle" })] }) }),
    JSON.stringify({ bridgeVersion: 1, ok: true, model: model({ sessions: [session({ hostId: "99999999-9999-4999-8999-999999999999" })] }) }),
    JSON.stringify({ bridgeVersion: 1, ok: false, error: {} })
]) {
    assert.strictEqual(modelApi.parseBridgeResponse(badEnvelope).ok, false)
}

console.log("SwitchboardModel.js: 21 deterministic behavior groups passed")
