const assert = require("assert")
const fs = require("fs")
const vm = require("vm")
const path = require("path")

const root = path.resolve(__dirname, "..")
const source = fs.readFileSync(path.join(root, "SwitchboardEntryModelV1.js"), "utf8")
    .replace(/^\.pragma library\s*/m, "")
const context = {}
vm.createContext(context)
vm.runInContext(source, context)

const host = "11111111-1111-4111-8111-111111111111"
const generation = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
const view = "22222222-2222-4222-8222-222222222222"
const project = "44444444-4444-4444-8444-444444444444"
const recovery = "55555555-5555-4555-8555-555555555555"
const request = "66666666-6666-4666-8666-666666666666"

function model() {
    return {
        modelVersion: 1,
        sourceNavigatorVersion: 1,
        sourceGenerationId: generation,
        generatedAt: 1000,
        localHostId: host,
        hosts: [{
            hostId: host,
            generationId: generation,
            displayName: "starship",
            isLocal: true,
            reachability: "online",
            stale: false,
            generatedAt: 999,
            activationState: "committed"
        }],
        views: [{
            hostId: host,
            viewId: view,
            mode: "direct",
            state: "ready",
            revision: 1,
            activeFrameId: "33333333-3333-4333-8333-333333333333",
            activeProjectId: project,
            title: "Implement Phase 6E",
            breadcrumb: ["Switchboard", "Implement Phase 6E"],
            activity: "ready",
            attention: "none",
            transitionState: null,
            controlState: null,
            lastActivityAt: 990
        }],
        projects: [{
            hostId: host,
            projectId: project,
            name: "Switchboard",
            viewId: view,
            entryFrameId: "33333333-3333-4333-8333-333333333333",
            frames: []
        }],
        recoveries: [{
            recoveryId: recovery,
            hostId: host,
            kind: "missing_tmux_container",
            subjectType: "view",
            subjectId: view,
            actionability: "open_view",
            state: "open",
            explanation: "The durable view needs repair.",
            createdAt: 980,
            updatedAt: 990
        }],
        warnings: [],
        truncation: {}
    }
}

const current = model()
assert.strictEqual(context.validateModel(current), true)
assert.strictEqual(context.launcherCategories(current).map(item => item.name).join(","), "Views,Projects,Recovery")

const state = { now: 1000, loading: false, stale: false, fresh: true, failure: null, category: "" }
let rows = context.launcherItems(current, "Phase", state)
assert.strictEqual(rows.length, 1)
assert.strictEqual(rows[0]._switchboardKind, "view")
assert.strictEqual(rows[0]._targetId, view)
assert.strictEqual(rows[0].badgeLabel, "View")

state.category = "projects"
rows = context.launcherItems(current, "Switch", state)
assert.strictEqual(rows.length, 1)
assert.strictEqual(rows[0]._switchboardKind, "project")

state.category = "recovery"
rows = context.launcherItems(current, "repair", state)
assert.strictEqual(rows.length, 1)
assert.strictEqual(rows[0]._switchboardKind, "recovery")
assert.strictEqual(rows[0]._actionability, "open_view")

const cached = context.cacheEnvelope(current)
assert.strictEqual(cached.adapterVersion, "0.5.0")
assert.strictEqual(context.cachedModel(cached).sourceGenerationId, generation)
assert.strictEqual(context.cachedModel(current), null)
cached.adapterVersion = "0.4.1"
assert.strictEqual(context.cachedModel(cached), null)

const action = context.parseActionResponse(JSON.stringify({
    actionVersion: 1,
    ok: true,
    action: { kind: "focused", hostId: host, viewId: view, requestId: request }
}))
assert.strictEqual(action.ok, true)
assert.strictEqual(action.action.kind, "focused")

console.log("entry-model-v1: ok")
