"use strict"

const assert = require("assert")
const fs = require("fs")
const path = require("path")
const vm = require("vm")

const root = path.resolve(__dirname, "..")
const source = fs.readFileSync(path.join(root, "SwitchboardModelV3.js"), "utf8")
    .replace(/^\.pragma library\s*$/m, "")
const modelApi = {}
vm.createContext(modelApi)
vm.runInContext(source, modelApi, { filename: "SwitchboardModelV3.js" })

const HOST_ID = "11111111-1111-4111-8111-111111111111"
const PROJECT_ID = "22222222-2222-4222-8222-222222222222"
const CHECKOUT_ID = "44444444-4444-4444-8444-444444444444"
const TASK_ID = "88888888-8888-4888-8888-888888888888"
const CODEX_SESSION = `${HOST_ID}:codex:55555555-5555-4555-8555-555555555555`
const CLAUDE_SESSION = `${HOST_ID}:claude:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa`

function project(overrides = {}) {
    return Object.assign({
        projectId: PROJECT_ID,
        name: "Agent Switchboard",
        repositoryName: "agent-switchboard",
        defaultProvider: "codex",
        defaultCheckoutId: CHECKOUT_ID
    }, overrides)
}

function task(overrides = {}) {
    return Object.assign({
        taskId: TASK_ID,
        projectId: PROJECT_ID,
        projectName: "Agent Switchboard",
        checkoutId: CHECKOUT_ID,
        checkoutName: "main",
        checkoutKind: "main",
        checkoutBranch: "main",
        checkoutIsDefault: true,
        title: "Refine the task picker",
        purpose: "Make project work concise and routable.",
        preferredProvider: "codex",
        status: "open",
        pinned: true,
        currentSessionKey: CODEX_SESSION,
        createdAt: 90000,
        updatedAt: 100000,
        closedAt: null,
        provider: "codex",
        runtimePresence: "live",
        resumability: "resumable",
        activity: "working",
        activityReason: "unknown",
        attachment: "detached",
        stateConfidence: "confirmed",
        recencyAt: 100000,
        canStop: false
    }, overrides)
}

function inbox(overrides = {}) {
    return Object.assign({
        sessionKey: CLAUDE_SESSION,
        providerSessionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        provider: "claude",
        projectId: PROJECT_ID,
        projectName: "Agent Switchboard",
        checkoutId: CHECKOUT_ID,
        checkoutName: "main",
        name: "unassigned review",
        runtimePresence: "live",
        resumability: "resumable",
        activity: "needs_input",
        activityReason: "question",
        attachment: "detached",
        stateConfidence: "confirmed",
        recencyAt: 95000,
        canStop: true
    }, overrides)
}

function model(overrides = {}) {
    return Object.assign({
        modelVersion: 3,
        sourceSchemaVersion: 2,
        sourceProtocolVersion: 2,
        generatedAt: 100000,
        host: { hostId: HOST_ID, displayName: "starship" },
        projects: [project()],
        tasks: [task()],
        inboxSessions: [inbox()],
        capabilities: [
            { provider: "codex", status: "available", available: true, features: [], degradedReasons: [] },
            { provider: "claude", status: "available", available: true, features: [], degradedReasons: [] }
        ],
        warnings: [],
        truncation: {}
    }, overrides)
}

function state(overrides = {}) {
    return Object.assign({ now: 101000, loading: false, stale: false, failure: null, category: "" }, overrides)
}

function kinds(items, kind) {
    return items.filter(item => item._switchboardKind === kind)
}

{
    assert.strictEqual(modelApi.validateModel(model()), true)
    assert.strictEqual(modelApi.validateModel(model({ tasks: Array(1001).fill(task()) })), false)
}

{
    const parsed = modelApi.parseBridgeResponse(JSON.stringify({ bridgeVersion: 2, ok: true, model: model() }))
    assert.strictEqual(parsed.ok, true)
    assert.strictEqual(parsed.model.modelVersion, 3)
}

{
    const categories = modelApi.launcherCategories(model())
    assert.deepStrictEqual(JSON.parse(JSON.stringify(categories)), [
        { id: "", name: "All tasks" },
        { id: `project:${PROJECT_ID}`, name: "Agent Switchboard" },
        { id: "inbox", name: "Inbox" },
        { id: "closed", name: "Closed" }
    ])
    assert.deepStrictEqual(JSON.parse(JSON.stringify(modelApi.launcherCategories(null))), [{ id: "", name: "All tasks" }])
}

{
    const items = modelApi.launcherItems(model(), "", state())
    const tasks = kinds(items, "task")
    assert.strictEqual(tasks.length, 1)
    assert.strictEqual(tasks[0].name, "Refine the task picker")
    assert.strictEqual(tasks[0].icon, "material:terminal")
    assert.strictEqual(tasks[0].comment, "Agent Switchboard | Working | now")
    assert.strictEqual(tasks[0].comment.includes("/work"), false)
    assert.strictEqual(kinds(items, "session").length, 0)
    assert.strictEqual(kinds(items, "status").some(item => item.id === "switchboard:status:inbox-summary"), true)
}

{
    const claudeTask = task({ provider: "claude", currentSessionKey: CLAUDE_SESSION, canStop: true })
    const noSession = task({
        taskId: "77777777-7777-4777-8777-777777777777",
        title: "Plan next phase",
        provider: null,
        currentSessionKey: null,
        activity: "unknown",
        runtimePresence: "unknown",
        recencyAt: 90000,
        pinned: false
    })
    const tasks = kinds(modelApi.launcherItems(model({ tasks: [claudeTask, noSession] }), "", state()), "task")
    assert.strictEqual(tasks[0].icon, "material:auto_awesome")
    assert.strictEqual(tasks[0]._canStop, true)
    assert.strictEqual(tasks[1].icon, "material:task_alt")
    assert.strictEqual(tasks[1].comment.includes("Not started"), true)
}

{
    const worktree = task({
        checkoutName: "picker-refine",
        checkoutKind: "worktree",
        checkoutBranch: "phase-4d-picker",
        checkoutIsDefault: false
    })
    const item = kinds(modelApi.launcherItems(model({ tasks: [worktree] }), "", state()), "task")[0]
    assert.strictEqual(item.comment, "Agent Switchboard | phase-4d-picker | Working | now")
}

{
    const closed = task({ status: "closed", closedAt: 100000, currentSessionKey: null, provider: null, canStop: false })
    assert.strictEqual(kinds(modelApi.launcherItems(model({ tasks: [closed] }), "", state()), "task").length, 0)
    const items = kinds(modelApi.launcherItems(model({ tasks: [closed] }), "", state({ category: "closed" })), "task")
    assert.strictEqual(items.length, 1)
    assert.strictEqual(items[0].comment.includes("Closed"), true)
}

{
    const items = modelApi.launcherItems(model(), "", state({ category: "inbox" }))
    const sessions = kinds(items, "session")
    assert.strictEqual(sessions.length, 1)
    assert.strictEqual(sessions[0].icon, "material:auto_awesome")
    assert.strictEqual(sessions[0].comment, "Agent Switchboard | Needs input | now")
    assert.strictEqual(sessions[0]._canStop, true)
}

{
    const category = `project:${PROJECT_ID}`
    assert.strictEqual(kinds(modelApi.launcherItems(model(), "", state({ category })), "create").length, 0)
    const creations = kinds(modelApi.launcherItems(model(), "Fix picker layout", state({ category })), "create")
    assert.strictEqual(creations.length, 2)
    assert.strictEqual(JSON.stringify(creations.map(item => item._provider).sort()), JSON.stringify(["claude", "codex"]))
    assert.strictEqual(creations[0]._title, "Fix picker layout")
    assert.strictEqual(creations[0]._checkoutId, CHECKOUT_ID)
    assert.strictEqual(kinds(modelApi.launcherItems(model(), "x".repeat(257), state({ category })), "create").length, 0)
    assert.strictEqual(kinds(modelApi.launcherItems(model(), "bad\nname", state({ category })), "create").length, 0)
}

{
    const match = kinds(modelApi.launcherItems(model(), "concise", state()), "task")
    assert.strictEqual(match.length, 1)
    assert.strictEqual(kinds(modelApi.launcherItems(model(), "missing", state()), "task").length, 0)
}

{
    const good = modelApi.parseActionResponse(JSON.stringify({
        actionVersion: 2,
        ok: true,
        action: { kind: "launched", surfaceId: "33333333-3333-4333-8333-333333333333" }
    }))
    assert.strictEqual(good.ok, true)
    const stopped = modelApi.parseActionResponse(JSON.stringify({
        actionVersion: 2,
        ok: true,
        action: { kind: "stopped", status: "stopped" }
    }))
    assert.strictEqual(stopped.ok, true)
}

{
    const badModels = [
        Object.assign({}, model(), { modelVersion: 2 }),
        Object.assign({}, model(), { sourceSchemaVersion: 1 }),
        Object.assign({}, model(), { tasks: [{}] }),
        Object.assign({}, model(), { inboxSessions: [{}] })
    ]
    for (const bad of badModels) {
        const parsed = modelApi.parseBridgeResponse(JSON.stringify({ bridgeVersion: 2, ok: true, model: bad }))
        assert.strictEqual(parsed.ok, false)
        assert.strictEqual(parsed.error.code, "bridge_invalid_model")
    }
}

{
    assert.strictEqual(modelApi.isStale(model(), 116000, 15), true)
    assert.strictEqual(modelApi.isStale(model(), 114999, 15), false)
    const queued = modelApi.planRunRequest({
        active: true,
        runWasRefresh: false,
        settingsGeneration: 1,
        runSettingsGeneration: 1,
        pendingRefresh: false,
        startScheduled: false
    }, true)
    assert.strictEqual(queued.queueRun, true)
    assert.strictEqual(queued.queueRefresh, true)
    assert.strictEqual(modelApi.shouldAcceptRunResult(2, 2, false, 0, true), true)
    assert.strictEqual(modelApi.shouldAcceptRunResult(1, 2, false, 0, true), false)
}

{
    assert.strictEqual(modelApi.stoppedRunDisposition({
        runActive: true,
        running: false,
        observedRunGeneration: 3,
        runGeneration: 3,
        settingsGeneration: 1,
        runSettingsGeneration: 1,
        exitFinished: false,
        runExpired: false
    }, false), "start_failed")
}

console.log("SwitchboardModelV3.js: 14 task-first behavior groups passed")
