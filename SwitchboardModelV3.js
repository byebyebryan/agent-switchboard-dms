// Version this physical module with the projected model contract. Reload-
// significant bridge-envelope and cache validation lives in the cache-busted
// launcher component so same-contract fixes do not depend on reloading JS.
var BRIDGE_VERSION = 2
var ACTION_VERSION = 2
var MODEL_VERSION = 3
var MAX_EXECUTABLE_LENGTH = 4096
var MAX_MODEL_PROJECTS = 1000
var MAX_MODEL_TASKS = 1000
var MAX_MODEL_SESSIONS = 1000
var MAX_MODEL_WARNINGS = 256

function boundedExecutable(value, fallback) {
    var defaultValue = String(fallback || "swbctl")
    return String(value || defaultValue).substring(0, MAX_EXECUTABLE_LENGTH)
}

function _object(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value)
}

function _string(value) {
    return typeof value === "string" && value.length > 0
}

function _optionalString(value) {
    return value === null || value === undefined || typeof value === "string"
}

function _timestamp(value) {
    return typeof value === "number" && isFinite(value) && value >= 0
}

function _oneOf(value, allowed) {
    return allowed.indexOf(value) !== -1
}

function _failure(code, message, retryable) {
    return {
        ok: false,
        error: {
            code: code,
            message: message,
            retryable: retryable === true
        }
    }
}

function _validateCapability(value, provider) {
    return _object(value)
        && value.provider === provider
        && _oneOf(value.status, ["available", "degraded", "neutral"])
        && (value.available === null || typeof value.available === "boolean")
        && Array.isArray(value.features)
        && Array.isArray(value.degradedReasons)
}

function _validateProject(value) {
    return _object(value)
        && _string(value.projectId)
        && _string(value.name)
        && _optionalString(value.repositoryName)
        && _oneOf(value.defaultProvider, ["codex", "claude"])
        && _optionalString(value.defaultCheckoutId)
}

function _validateTask(value) {
    if (!_object(value) || !_string(value.taskId) || !_string(value.projectId))
        return false
    if (!_string(value.projectName) || !_string(value.title))
        return false
    if (!_optionalString(value.checkoutId) || !_optionalString(value.checkoutName))
        return false
    if (!_optionalString(value.checkoutKind) || !_optionalString(value.checkoutBranch))
        return false
    if (value.checkoutKind !== null && !_oneOf(value.checkoutKind, ["main", "worktree", "directory"]))
        return false
    if (typeof value.checkoutIsDefault !== "boolean" || typeof value.pinned !== "boolean")
        return false
    if (!_optionalString(value.purpose) || !_optionalString(value.preferredProvider))
        return false
    if (value.preferredProvider !== null && !_oneOf(value.preferredProvider, ["codex", "claude"]))
        return false
    if (!_oneOf(value.status, ["open", "closed"]) || !_optionalString(value.currentSessionKey))
        return false
    if (!_timestamp(value.createdAt) || !_timestamp(value.updatedAt))
        return false
    if (value.closedAt !== null && !_timestamp(value.closedAt))
        return false
    if (!_optionalString(value.provider) || !_oneOf(value.runtimePresence, ["live", "stopped", "unknown"]))
        return false
    if (value.provider !== null && !_oneOf(value.provider, ["codex", "claude"]))
        return false
    if (!_oneOf(value.resumability, ["resumable", "missing", "unknown"]))
        return false
    if (!_oneOf(value.activity, ["working", "needs_input", "ready", "completed", "unknown"]))
        return false
    if (!_oneOf(value.activityReason, ["permission", "question", "elicitation", "turn_complete", "provider_complete", "error", "unknown"]))
        return false
    if (!_oneOf(value.attachment, ["attached", "detached", "none", "unknown"]))
        return false
    if (!_oneOf(value.stateConfidence, ["confirmed", "inferred", "unknown"]))
        return false
    return _timestamp(value.recencyAt) && typeof value.canStop === "boolean"
}

function _validateInboxSession(value) {
    if (!_object(value) || !_string(value.sessionKey) || !_string(value.providerSessionId))
        return false
    if (!_oneOf(value.provider, ["codex", "claude"]))
        return false
    if (!_optionalString(value.projectId) || !_optionalString(value.projectName))
        return false
    if (!_optionalString(value.checkoutId) || !_optionalString(value.checkoutName) || !_optionalString(value.name))
        return false
    if (!_oneOf(value.runtimePresence, ["live", "stopped", "unknown"]))
        return false
    if (!_oneOf(value.resumability, ["resumable", "missing", "unknown"]))
        return false
    if (!_oneOf(value.activity, ["working", "needs_input", "ready", "completed", "unknown"]))
        return false
    if (!_oneOf(value.activityReason, ["permission", "question", "elicitation", "turn_complete", "provider_complete", "error", "unknown"]))
        return false
    if (!_oneOf(value.attachment, ["attached", "detached", "none", "unknown"]))
        return false
    if (!_oneOf(value.stateConfidence, ["confirmed", "inferred", "unknown"]))
        return false
    return _timestamp(value.recencyAt) && typeof value.canStop === "boolean"
}

function validateModel(model) {
    if (!_object(model) || model.modelVersion !== MODEL_VERSION)
        return false
    if (model.sourceSchemaVersion !== 2 || model.sourceProtocolVersion !== 2)
        return false
    if (!_timestamp(model.generatedAt) || !_object(model.host))
        return false
    if (!_string(model.host.hostId) || !_string(model.host.displayName))
        return false
    if (!Array.isArray(model.projects) || !Array.isArray(model.tasks) || !Array.isArray(model.inboxSessions))
        return false
    if (model.projects.length > MAX_MODEL_PROJECTS || model.tasks.length > MAX_MODEL_TASKS || model.inboxSessions.length > MAX_MODEL_SESSIONS)
        return false
    if (!Array.isArray(model.capabilities) || model.capabilities.length !== 2)
        return false
    if (!Array.isArray(model.warnings) || !_object(model.truncation))
        return false
    if (model.warnings.length > MAX_MODEL_WARNINGS)
        return false
    if (!_validateCapability(model.capabilities[0], "codex") || !_validateCapability(model.capabilities[1], "claude"))
        return false
    var identities = {}
    for (var projectIndex = 0; projectIndex < model.projects.length; projectIndex++) {
        var project = model.projects[projectIndex]
        if (!_validateProject(project) || identities["project:" + project.projectId])
            return false
        identities["project:" + project.projectId] = true
    }
    for (var taskIndex = 0; taskIndex < model.tasks.length; taskIndex++) {
        var task = model.tasks[taskIndex]
        if (!_validateTask(task) || identities["task:" + task.taskId] || !identities["project:" + task.projectId])
            return false
        identities["task:" + task.taskId] = true
    }
    for (var inboxIndex = 0; inboxIndex < model.inboxSessions.length; inboxIndex++) {
        var session = model.inboxSessions[inboxIndex]
        if (!_validateInboxSession(session) || identities["session:" + session.sessionKey])
            return false
        identities["session:" + session.sessionKey] = true
    }
    return true
}

function parseBridgeResponse(text) {
    var envelope
    try {
        envelope = JSON.parse(String(text))
    } catch (error) {
        return _failure("bridge_invalid_json", "The bridge returned an invalid response.", false)
    }
    if (!_object(envelope) || envelope.bridgeVersion !== BRIDGE_VERSION)
        return _failure("bridge_incompatible", "The bridge response is incompatible.", false)
    if (envelope.ok === false) {
        if (!_object(envelope.error) || !_string(envelope.error.code) || !_string(envelope.error.message))
            return _failure("bridge_invalid_error", "The bridge returned an invalid error.", false)
        return _failure(envelope.error.code, envelope.error.message, envelope.error.retryable)
    }
    if (envelope.ok !== true || !validateModel(envelope.model))
        return _failure("bridge_invalid_model", "The bridge returned an invalid model.", false)
    return { ok: true, model: envelope.model }
}

function parseActionResponse(text) {
    var envelope
    try {
        envelope = JSON.parse(String(text))
    } catch (error) {
        return _failure("action_invalid_json", "The session opener returned an invalid response.", false)
    }
    if (!_object(envelope) || envelope.actionVersion !== ACTION_VERSION)
        return _failure("action_incompatible", "The session opener response is incompatible.", false)
    if (envelope.ok === false) {
        if (!_object(envelope.error) || !_string(envelope.error.code) || !_string(envelope.error.message))
            return _failure("action_invalid_error", "The session opener returned an invalid error.", false)
        return _failure(envelope.error.code, envelope.error.message, envelope.error.retryable)
    }
    if (envelope.ok !== true || !_object(envelope.action))
        return _failure("action_invalid_result", "The session opener returned an invalid result.", false)
    if (envelope.action.kind === "stopped") {
        if (!_oneOf(envelope.action.status, ["stopped", "already_stopped"]))
            return _failure("action_invalid_result", "The session opener returned an invalid result.", false)
        return { ok: true, action: envelope.action }
    }
    if (!_oneOf(envelope.action.kind, ["focused", "switched", "launched"]) || !_string(envelope.action.surfaceId))
        return _failure("action_invalid_result", "The session opener returned an invalid result.", false)
    return { ok: true, action: envelope.action }
}

function launcherCategories(model) {
    var result = [{ id: "", name: "All tasks" }]
    if (!validateModel(model))
        return result
    for (var index = 0; index < model.projects.length; index++)
        result.push({ id: "project:" + model.projects[index].projectId, name: model.projects[index].name })
    result.push({ id: "inbox", name: "Inbox" })
    result.push({ id: "closed", name: "Closed" })
    return result
}

function _age(timestamp, now) {
    var seconds = Math.max(0, Math.floor((now - timestamp) / 1000))
    if (seconds < 60)
        return "now"
    var minutes = Math.floor(seconds / 60)
    if (minutes < 60)
        return String(minutes) + "m"
    var hours = Math.floor(minutes / 60)
    if (hours < 24)
        return String(hours) + "h"
    return String(Math.floor(hours / 24)) + "d"
}

function _stateLabel(value) {
    if (value.status === "closed")
        return "Closed"
    if (!value.currentSessionKey)
        return "Not started"
    if (value.activity === "needs_input")
        return "Needs input"
    if (value.activity === "working")
        return "Working"
    if (value.activity === "ready")
        return "Ready"
    if (value.activity === "completed")
        return "Done"
    if (value.runtimePresence === "stopped")
        return value.resumability === "resumable" ? "Resumable" : "Stopped"
    return "State unknown"
}

function _providerIcon(provider) {
    if (provider === "claude")
        return "material:auto_awesome"
    if (provider === "codex")
        return "material:terminal"
    return "material:task_alt"
}

function _taskSearchText(task) {
    return [task.title, task.purpose, task.projectName, task.checkoutName, task.checkoutBranch, task.taskId, task.provider]
        .filter(function(value) { return typeof value === "string" })
        .join("\n")
        .toLowerCase()
}

function _taskItem(task, host, now, index) {
    var comment = [task.projectName]
    if (!task.checkoutIsDefault && task.checkoutKind === "worktree")
        comment.push(task.checkoutBranch || task.checkoutName || "worktree")
    comment.push(_stateLabel(task))
    comment.push(_age(task.recencyAt, now))
    return {
        id: "switchboard:task:" + task.taskId,
        name: task.title,
        icon: _providerIcon(task.provider),
        comment: comment.join(" | "),
        categories: ["Switchboard"],
        keywords: [task.taskId, task.projectId],
        _preScored: (task.pinned ? 4000 : 3000) - index,
        _switchboardKind: "task",
        _taskId: task.taskId,
        _projectId: task.projectId,
        _checkoutId: task.checkoutId,
        _sessionKey: task.currentSessionKey,
        _provider: task.provider,
        _canStop: task.canStop,
        _windowHost: host.displayName
    }
}

function _inboxSearchText(session) {
    return [session.name, session.projectName, session.checkoutName, session.sessionKey, session.providerSessionId, session.provider]
        .filter(function(value) { return typeof value === "string" })
        .join("\n")
        .toLowerCase()
}

function _inboxItem(session, host, now, index) {
    var name = session.name || (session.provider === "claude" ? "Claude " : "Codex ") + session.providerSessionId.substring(0, 8)
    var comment = []
    if (_string(session.projectName))
        comment.push(session.projectName)
    comment.push(_stateLabel({
        status: "open",
        currentSessionKey: session.sessionKey,
        activity: session.activity,
        runtimePresence: session.runtimePresence,
        resumability: session.resumability
    }))
    comment.push(_age(session.recencyAt, now))
    return {
        id: "switchboard:inbox:" + session.sessionKey,
        name: name,
        icon: _providerIcon(session.provider),
        comment: comment.join(" | "),
        categories: ["Switchboard"],
        keywords: [session.sessionKey, session.providerSessionId],
        _preScored: 2500 - index,
        _switchboardKind: "session",
        _sessionKey: session.sessionKey,
        _projectId: session.projectId,
        _checkoutId: session.checkoutId,
        _provider: session.provider,
        _canStop: session.canStop,
        _windowHost: host.displayName
    }
}

function _createItem(project, provider, title, host, index) {
    var providerName = provider === "claude" ? "Claude" : "Codex"
    return {
        id: "switchboard:create:" + provider + ":" + project.projectId + ":" + title,
        name: "New " + providerName + " — " + title,
        icon: _providerIcon(provider),
        comment: project.name + " | Create and open task",
        categories: ["Switchboard"],
        keywords: [project.projectId, provider, title],
        _preScored: 5000 - index,
        _switchboardKind: "create",
        _projectId: project.projectId,
        _checkoutId: project.defaultCheckoutId,
        _provider: provider,
        _title: title,
        _windowHost: host.displayName
    }
}

function _validTaskTitle(value) {
    return value.length > 0 && value.length <= 256 && !/[\u0000-\u001f\u007f]/.test(value)
}

function _statusItem(kind, name, comment, score) {
    return {
        id: "switchboard:status:" + kind,
        name: name,
        icon: "material:info",
        comment: comment,
        categories: ["Switchboard"],
        _preScored: score,
        _switchboardKind: "status"
    }
}

function isStale(model, now, refreshSeconds) {
    return !validateModel(model) || now - model.generatedAt >= refreshSeconds * 1000
}

function planRunRequest(state, refresh) {
    if (state.active) {
        if (state.settingsGeneration !== state.runSettingsGeneration) {
            return { pendingRefresh: state.pendingRefresh, queueRun: true, queueRefresh: state.runWasRefresh || refresh, shouldSchedule: false }
        }
        if (refresh && !state.runWasRefresh) {
            return { pendingRefresh: state.pendingRefresh, queueRun: true, queueRefresh: true, shouldSchedule: false }
        }
        return { pendingRefresh: state.pendingRefresh, queueRun: false, queueRefresh: false, shouldSchedule: false }
    }
    return { pendingRefresh: state.pendingRefresh || refresh, queueRun: false, queueRefresh: false, shouldSchedule: !state.startScheduled }
}

function stoppedRunDisposition(state, deadline) {
    if (!state.runActive || state.running || state.observedRunGeneration !== state.runGeneration)
        return "none"
    if (state.settingsGeneration !== state.runSettingsGeneration)
        return "stale"
    if (state.exitFinished && !deadline)
        return "wait"
    if (state.runExpired)
        return "expired"
    return state.exitFinished ? "incomplete" : "start_failed"
}

function shouldAcceptRunResult(runSettingsGeneration, settingsGeneration, runExpired, exitCode, parsedOk) {
    return runSettingsGeneration === settingsGeneration && !runExpired && exitCode === 0 && parsedOk === true
}

function launcherItems(model, query, state) {
    var now = state.now
    if (model === null || model === undefined) {
        if (state.loading)
            return [_statusItem("loading", "Loading Switchboard tasks", "Reading a validated local snapshot…", 5000)]
        if (state.failure)
            return [_statusItem("error", "Switchboard snapshot unavailable", state.failure.message, 5000)]
        return [_statusItem("initial", "Switchboard has not loaded yet", "A background snapshot read will start shortly.", 5000)]
    }

    var result = []
    if (state.failure)
        result.push(_statusItem("degraded-refresh", "Refresh failed — showing last good snapshot", state.failure.message, 5000))
    else if (state.loading)
        result.push(_statusItem("refreshing", "Refreshing Switchboard tasks", "Showing the last good snapshot while refresh runs.", 5000))
    else if (state.stale)
        result.push(_statusItem("stale", "Switchboard snapshot is stale", "Showing retained source-authored state.", 5000))

    var normalizedQuery = String(query || "").trim().toLowerCase()
    var category = String(state.category || "")
    if (category === "inbox") {
        for (var inboxIndex = 0; inboxIndex < model.inboxSessions.length; inboxIndex++) {
            var session = model.inboxSessions[inboxIndex]
            if (!normalizedQuery || _inboxSearchText(session).indexOf(normalizedQuery) !== -1)
                result.push(_inboxItem(session, model.host, now, inboxIndex))
        }
    } else {
        for (var taskIndex = 0; taskIndex < model.tasks.length; taskIndex++) {
            var task = model.tasks[taskIndex]
            var categoryMatch = category === "closed"
                ? task.status === "closed"
                : category.indexOf("project:") === 0
                    ? task.status === "open" && task.projectId === category.substring(8)
                    : task.status === "open"
            if (categoryMatch && (!normalizedQuery || _taskSearchText(task).indexOf(normalizedQuery) !== -1))
                result.push(_taskItem(task, model.host, now, taskIndex))
        }
        var creationTitle = String(query || "").trim()
        if (category.indexOf("project:") === 0 && _validTaskTitle(creationTitle)) {
            var projectId = category.substring(8)
            for (var projectIndex = 0; projectIndex < model.projects.length; projectIndex++) {
                var project = model.projects[projectIndex]
                if (project.projectId === projectId && project.defaultCheckoutId) {
                    result.push(_createItem(project, "codex", creationTitle, model.host, 0))
                    result.push(_createItem(project, "claude", creationTitle, model.host, 1))
                    break
                }
            }
        }
        if (!category && model.inboxSessions.length > 0) {
            result.push(_statusItem(
                "inbox-summary",
                "Inbox — " + model.inboxSessions.length + " unassigned session" + (model.inboxSessions.length === 1 ? "" : "s"),
                "Choose the Inbox category to review or adopt them.",
                1000
            ))
        }
    }

    if (result.length === 0)
        result.push(_statusItem("empty", "No matching Switchboard items", "Try another category or task title.", 3000))
    return result
}
