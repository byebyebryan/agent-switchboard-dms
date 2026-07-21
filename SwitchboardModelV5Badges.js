// Fleet-aware DMS projection with provider badges and session-state icons.
var BRIDGE_VERSION = 4
var ACTION_VERSION = 4
var MODEL_VERSION = 5
var MAX_EXECUTABLE_LENGTH = 4096
var MAX_MODEL_HOSTS = 33
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
    return { ok: false, error: { code: code, message: message, retryable: retryable === true } }
}

function _validateHost(value) {
    return _object(value)
        && _oneOf(value.source, ["local", "remote"])
        && _optionalString(value.remoteName)
        && (value.hostId === null || _string(value.hostId))
        && _string(value.displayName)
        && _oneOf(value.reachability, ["online", "offline", "unknown"])
        && typeof value.stale === "boolean"
        && typeof value.hasSnapshot === "boolean"
        && (value.error === null || (_object(value.error) && _string(value.error.code)
            && _string(value.error.message) && typeof value.error.retryable === "boolean"))
}

function _validateRoute(value) {
    return _object(value) && _string(value.hostId) && _string(value.hostDisplayName)
        && typeof value.isLocal === "boolean"
        && _oneOf(value.defaultProvider, ["codex", "claude"])
        && _optionalString(value.defaultCheckoutId)
        && _oneOf(value.reachability, ["online", "offline", "unknown"])
        && typeof value.stale === "boolean"
}

function _validateProject(value) {
    if (!_object(value) || !_string(value.projectId) || !_string(value.name)
            || !_optionalString(value.repositoryName) || !Array.isArray(value.routes)
            || value.routes.length > MAX_MODEL_HOSTS)
        return false
    var hosts = {}
    for (var index = 0; index < value.routes.length; index++) {
        var route = value.routes[index]
        if (!_validateRoute(route) || hosts[route.hostId])
            return false
        hosts[route.hostId] = true
    }
    return true
}

function _validateTask(value) {
    if (!_object(value) || !_string(value.taskId) || !_string(value.projectId)
            || !_string(value.projectName) || !_string(value.title))
        return false
    if (!_optionalString(value.checkoutId) || !_optionalString(value.checkoutName)
            || !_optionalString(value.checkoutKind) || !_optionalString(value.checkoutBranch))
        return false
    if (value.checkoutKind !== null && !_oneOf(value.checkoutKind, ["main", "worktree", "directory"]))
        return false
    if (typeof value.checkoutIsDefault !== "boolean" || typeof value.pinned !== "boolean"
            || typeof value.canStop !== "boolean")
        return false
    if (!_optionalString(value.purpose) || !_optionalString(value.preferredProvider)
            || (value.preferredProvider !== null && !_oneOf(value.preferredProvider, ["codex", "claude"])))
        return false
    if (!_oneOf(value.status, ["open", "closed"]) || !_optionalString(value.currentSessionKey)
            || !_timestamp(value.createdAt) || !_timestamp(value.updatedAt)
            || (value.closedAt !== null && !_timestamp(value.closedAt)))
        return false
    if ((value.status === "closed") !== (value.closedAt !== null))
        return false
    if (!_optionalString(value.provider)
            || (value.provider !== null && !_oneOf(value.provider, ["codex", "claude"])))
        return false
    if (!_oneOf(value.runtimePresence, ["live", "stopped", "unknown"])
            || !_oneOf(value.resumability, ["resumable", "missing", "unknown"])
            || !_oneOf(value.activity, ["working", "needs_input", "ready", "completed", "unknown"])
            || !_oneOf(value.activityReason, ["permission", "question", "elicitation", "turn_complete", "provider_complete", "error", "unknown"])
            || !_oneOf(value.attachment, ["attached", "detached", "none", "unknown"])
            || !_oneOf(value.stateConfidence, ["confirmed", "inferred", "unknown"]))
        return false
    if ((value.currentSessionKey === null) !== (value.provider === null))
        return false
    if (value.canStop && value.runtimePresence !== "live")
        return false
    return _timestamp(value.recencyAt) && _string(value.hostId)
        && _string(value.hostDisplayName) && typeof value.isLocal === "boolean"
        && _oneOf(value.hostReachability, ["online", "offline", "unknown"])
        && typeof value.hostStale === "boolean"
}

function _validateInbox(value) {
    if (!_object(value) || !_string(value.sessionKey) || !_string(value.providerSessionId)
            || !_oneOf(value.provider, ["codex", "claude"]))
        return false
    if (!_optionalString(value.projectId) || !_optionalString(value.projectName)
            || !_optionalString(value.checkoutId) || !_optionalString(value.checkoutName)
            || !_optionalString(value.name))
        return false
    if (!_oneOf(value.runtimePresence, ["live", "stopped", "unknown"])
            || !_oneOf(value.resumability, ["resumable", "missing", "unknown"])
            || !_oneOf(value.activity, ["working", "needs_input", "ready", "completed", "unknown"])
            || !_oneOf(value.activityReason, ["permission", "question", "elicitation", "turn_complete", "provider_complete", "error", "unknown"])
            || !_oneOf(value.attachment, ["attached", "detached", "none", "unknown"])
            || !_oneOf(value.stateConfidence, ["confirmed", "inferred", "unknown"]))
        return false
    if (value.canStop && value.runtimePresence !== "live")
        return false
    return _timestamp(value.recencyAt) && typeof value.canStop === "boolean"
        && _string(value.hostId) && _string(value.hostDisplayName)
        && typeof value.isLocal === "boolean"
        && _oneOf(value.hostReachability, ["online", "offline", "unknown"])
        && typeof value.hostStale === "boolean"
}

function _routeMatchesHost(value, host) {
    return value.hostDisplayName === host.displayName
        && value.isLocal === (host.source === "local")
        && value.reachability === host.reachability
        && value.stale === host.stale
}

function _validateWarning(value, hosts) {
    return _object(value) && (value.hostId === null || (_string(value.hostId) && hosts[value.hostId]))
        && _oneOf(value.source, ["capability", "error", "fleet", "model"])
        && (value.provider === undefined || value.provider === null || _oneOf(value.provider, ["codex", "claude"]))
        && _string(value.code) && _string(value.message) && typeof value.retryable === "boolean"
}

function _validateTruncation(value, model) {
    return _object(value)
        && Number.isInteger(value.sourceHostCount) && value.sourceHostCount >= model.hosts.length
        && value.emittedHostCount === model.hosts.length
        && Number.isInteger(value.sourceTaskCount) && value.sourceTaskCount >= model.tasks.length
        && value.emittedTaskCount === model.tasks.length && typeof value.tasksTruncated === "boolean"
        && Number.isInteger(value.sourceInboxCount) && value.sourceInboxCount >= model.inboxSessions.length
        && value.emittedInboxCount === model.inboxSessions.length && typeof value.inboxTruncated === "boolean"
        && Number.isInteger(value.sessionLimit) && value.sessionLimit >= 1
        && value.sessionLimit <= MAX_MODEL_SESSIONS
}

function validateModel(model) {
    if (!_object(model) || model.modelVersion !== MODEL_VERSION
            || model.sourceSchemaVersion !== 2 || model.sourceProtocolVersion !== 2
            || model.sourceFleetVersion !== 1 || !_timestamp(model.generatedAt)
            || !_string(model.localHostId))
        return false
    if (!Array.isArray(model.hosts) || model.hosts.length < 1 || model.hosts.length > MAX_MODEL_HOSTS
            || !Array.isArray(model.projects) || model.projects.length > MAX_MODEL_PROJECTS
            || !Array.isArray(model.tasks) || model.tasks.length > MAX_MODEL_TASKS
            || !Array.isArray(model.inboxSessions) || model.inboxSessions.length > MAX_MODEL_SESSIONS
            || !Array.isArray(model.warnings) || model.warnings.length > MAX_MODEL_WARNINGS
            || !_object(model.truncation))
        return false
    var identities = {}
    var hosts = {}
    var localFound = false
    for (var hostIndex = 0; hostIndex < model.hosts.length; hostIndex++) {
        var host = model.hosts[hostIndex]
        if (!_validateHost(host))
            return false
        if (host.source === "local") {
            if (hostIndex !== 0 || host.remoteName !== null || host.hostId !== model.localHostId
                    || !host.hasSnapshot || host.reachability !== "online" || host.stale || host.error !== null)
                return false
        } else if (hostIndex === 0 || !_string(host.remoteName))
            return false
        if (host.hostId === null && host.hasSnapshot)
            return false
        if (host.reachability === "online" && (!host.hasSnapshot || host.error !== null))
            return false
        if (host.reachability === "offline" && host.error === null)
            return false
        if (host.hostId !== null) {
            if (identities["host:" + host.hostId])
                return false
            identities["host:" + host.hostId] = true
            hosts[host.hostId] = host
            if (host.source === "local" && host.hostId === model.localHostId)
                localFound = true
        }
    }
    if (!localFound)
        return false
    var projectRoutes = {}
    var projects = {}
    for (var projectIndex = 0; projectIndex < model.projects.length; projectIndex++) {
        var project = model.projects[projectIndex]
        if (!_validateProject(project) || identities["project:" + project.projectId])
            return false
        identities["project:" + project.projectId] = true
        if (project.routes.length === 0)
            return false
        projects[project.projectId] = project
        projectRoutes[project.projectId] = {}
        for (var routeIndex = 0; routeIndex < project.routes.length; routeIndex++) {
            var route = project.routes[routeIndex]
            if (!identities["host:" + route.hostId] || !_routeMatchesHost(route, hosts[route.hostId]))
                return false
            projectRoutes[project.projectId][route.hostId] = true
        }
    }
    var assignedSessions = {}
    for (var taskIndex = 0; taskIndex < model.tasks.length; taskIndex++) {
        var task = model.tasks[taskIndex]
        var taskKey = "task:" + task.hostId + ":" + task.taskId
        if (!_validateTask(task) || identities[taskKey]
                || !identities["host:" + task.hostId]
                || !identities["project:" + task.projectId]
                || !projectRoutes[task.projectId][task.hostId]
                || task.projectName !== projects[task.projectId].name
                || !_routeMatchesHost({
                    hostDisplayName: task.hostDisplayName,
                    isLocal: task.isLocal,
                    reachability: task.hostReachability,
                    stale: task.hostStale
                }, hosts[task.hostId]))
            return false
        if (task.currentSessionKey !== null) {
            if (assignedSessions[task.currentSessionKey])
                return false
            assignedSessions[task.currentSessionKey] = true
        }
        identities[taskKey] = true
    }
    for (var inboxIndex = 0; inboxIndex < model.inboxSessions.length; inboxIndex++) {
        var session = model.inboxSessions[inboxIndex]
        if (!_validateInbox(session) || identities["session:" + session.sessionKey]
                || assignedSessions[session.sessionKey] || !identities["host:" + session.hostId]
                || (session.projectId !== null && (!_string(session.projectName)
                    || (projects[session.projectId]
                        && (!projectRoutes[session.projectId][session.hostId]
                            || session.projectName !== projects[session.projectId].name))))
                || (session.projectId === null && session.projectName !== null)
                || !_routeMatchesHost({
                    hostDisplayName: session.hostDisplayName,
                    isLocal: session.isLocal,
                    reachability: session.hostReachability,
                    stale: session.hostStale
                }, hosts[session.hostId]))
            return false
        identities["session:" + session.sessionKey] = true
    }
    for (var warningIndex = 0; warningIndex < model.warnings.length; warningIndex++)
        if (!_validateWarning(model.warnings[warningIndex], hosts))
            return false
    return _validateTruncation(model.truncation, model)
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
    if (envelope.action.kind === "closed") {
        if (!_oneOf(envelope.action.status, ["closed", "already_closed"])
                || !_string(envelope.action.taskId)
                || !_oneOf(envelope.action.runtimeDisposition,
                    ["no_session", "already_stopped", "stopped", "retained", "unknown"])
                || (envelope.action.warning !== undefined
                    && (!_object(envelope.action.warning) || !_string(envelope.action.warning.code)
                        || !_string(envelope.action.warning.message)
                        || typeof envelope.action.warning.retryable !== "boolean")))
            return _failure("action_invalid_result", "The session opener returned an invalid result.", false)
        return { ok: true, action: envelope.action }
    }
    if (!_oneOf(envelope.action.kind, ["focused", "switched", "launched"])
            || !_string(envelope.action.surfaceId))
        return _failure("action_invalid_result", "The session opener returned an invalid result.", false)
    return { ok: true, action: envelope.action }
}

function launcherCategories(model) {
    var result = [
        { id: "", name: "All tasks" },
        { id: "projects", name: "Projects" }
    ]
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
    if (value.hostReachability === "offline")
        return "Offline"
    if (value.status === "closed") {
        if (value.runtimePresence === "live")
            return "Closed · runtime live"
        if (value.runtimePresence === "unknown")
            return "Closed · runtime unknown"
        return "Closed"
    }
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

function _providerLabel(provider) {
    if (provider === "claude")
        return "Claude"
    if (provider === "codex")
        return "Codex"
    return ""
}

function _stateIcon(value) {
    if (value.hostReachability === "offline")
        return "material:cloud_off"
    if (value.status === "closed")
        return value.runtimePresence === "stopped" ? "material:archive" : "material:warning"
    if (!value.currentSessionKey)
        return "material:add_circle"
    if (value.activity === "needs_input")
        return "material:priority_high"
    if (value.activity === "working")
        return "material:sync"
    if (value.activity === "ready" || value.activity === "completed")
        return "material:check_circle"
    if (value.runtimePresence === "stopped")
        return value.resumability === "resumable" ? "material:history" : "material:stop_circle"
    if (value.runtimePresence === "live")
        return "material:terminal"
    return "material:help_outline"
}

function _taskSearchText(task) {
    return [task.title, task.purpose, task.projectName, task.checkoutName,
        task.checkoutBranch, task.taskId, task.provider, task.preferredProvider, task.hostDisplayName]
        .filter(function(value) { return typeof value === "string" }).join("\n").toLowerCase()
}

function _taskItem(task, now, index) {
    var comment = [task.projectName]
    if (!task.isLocal)
        comment.push(task.hostDisplayName)
    if (!task.checkoutIsDefault && task.checkoutKind === "worktree")
        comment.push(task.checkoutBranch || task.checkoutName || "worktree")
    comment.push(_stateLabel(task))
    if (task.hostStale && task.hostReachability !== "offline")
        comment.push("Stale")
    comment.push(_age(task.recencyAt, now))
    return {
        id: "switchboard:task:" + task.hostId + ":" + task.taskId,
        name: task.title,
        icon: _stateIcon(task),
        badgeLabel: _providerLabel(task.provider || task.preferredProvider) || "Task",
        comment: comment.join(" | "),
        categories: ["Switchboard"],
        keywords: [task.taskId, task.projectId, task.provider || task.preferredProvider,
            task.hostDisplayName],
        _preScored: (task.pinned ? 4000 : 3000) - index,
        _switchboardKind: "task",
        _hostId: task.hostId,
        _taskId: task.taskId,
        _projectId: task.projectId,
        _checkoutId: task.checkoutId,
        _sessionKey: task.currentSessionKey,
        _provider: task.provider,
        _status: task.status,
        _canStop: task.canStop,
        _windowHost: task.hostDisplayName
    }
}

function _inboxSearchText(session) {
    return [session.name, session.projectName, session.checkoutName, session.sessionKey,
        session.providerSessionId, session.provider, session.hostDisplayName]
        .filter(function(value) { return typeof value === "string" }).join("\n").toLowerCase()
}

function _inboxItem(session, now, index) {
    var name = session.name || (session.provider === "claude" ? "Claude " : "Codex ")
        + session.providerSessionId.substring(0, 8)
    var comment = []
    if (_string(session.projectName))
        comment.push(session.projectName)
    if (!session.isLocal)
        comment.push(session.hostDisplayName)
    var state = {
        status: "open", currentSessionKey: session.sessionKey, activity: session.activity,
        runtimePresence: session.runtimePresence, resumability: session.resumability,
        hostReachability: session.hostReachability
    }
    comment.push(_stateLabel(state))
    if (session.hostStale && session.hostReachability !== "offline")
        comment.push("Stale")
    comment.push(_age(session.recencyAt, now))
    return {
        id: "switchboard:inbox:" + session.sessionKey,
        name: name,
        icon: _stateIcon(state),
        badgeLabel: _providerLabel(session.provider),
        comment: comment.join(" | "),
        categories: ["Switchboard"],
        keywords: [session.sessionKey, session.providerSessionId, session.provider,
            session.hostDisplayName],
        _preScored: 2500 - index,
        _switchboardKind: "session",
        _hostId: session.hostId,
        _sessionKey: session.sessionKey,
        _projectId: session.projectId,
        _checkoutId: session.checkoutId,
        _provider: session.provider,
        _canStop: session.canStop,
        _windowHost: session.hostDisplayName
    }
}

function _createItem(project, route, provider, title, index, qualifyHost) {
    var hostSuffix = qualifyHost ? " on " + route.hostDisplayName : ""
    var comment = [project.name]
    if (!route.isLocal)
        comment.push(route.hostDisplayName)
    comment.push(route.reachability === "offline" ? "Offline" : "Create and open task")
    return {
        id: "switchboard:create:" + route.hostId + ":" + provider + ":" + project.projectId + ":" + title,
        name: "New" + hostSuffix + " — " + title,
        icon: route.reachability === "offline" ? "material:cloud_off" : "material:add_circle",
        badgeLabel: _providerLabel(provider),
        comment: comment.join(" | "),
        categories: ["Switchboard"],
        keywords: [project.projectId, provider, title, route.hostDisplayName],
        _preScored: 5000 - index,
        _switchboardKind: "create",
        _hostId: route.hostId,
        _projectId: project.projectId,
        _checkoutId: route.defaultCheckoutId,
        _provider: provider,
        _title: title,
        _windowHost: route.hostDisplayName
    }
}

function _validTaskTitle(value) {
    return value.length > 0 && value.length <= 256 && !/[\u0000-\u001f\u007f]/.test(value)
}

function _statusItem(kind, name, comment, score) {
    return { id: "switchboard:status:" + kind, name: name, icon: "material:info",
        comment: comment, categories: ["Switchboard"], _preScored: score,
        _switchboardKind: "status" }
}

function _projectManagerItem(project, index) {
    var comment = []
    if (_string(project.repositoryName))
        comment.push(project.repositoryName)
    comment.push("Manage project")
    return {
        id: "switchboard:manage-project:" + project.projectId,
        name: project.name,
        icon: "material:folder_code",
        comment: comment.join(" | "),
        categories: ["Switchboard"],
        keywords: [project.projectId, project.repositoryName || ""],
        _preScored: 4000 - index,
        _switchboardKind: "project-manager",
        _projectId: project.projectId
    }
}

function _projectActionItem(kind) {
    if (kind === "add")
        return {
            id: "switchboard:add-project",
            name: "Add project",
            icon: "material:create_new_folder",
            comment: "Register a Git repository or directory",
            categories: ["Switchboard"],
            keywords: ["new", "create", "register", "repository", "directory"],
            _preScored: 5000,
            _switchboardKind: "project-add"
        }
    return {
        id: "switchboard:manage-projects",
        name: "Manage projects",
        icon: "material:settings",
        comment: "Open the full project catalog",
        categories: ["Switchboard"],
        keywords: ["catalog", "repositories", "checkouts", "worktrees"],
        _preScored: 1000,
        _switchboardKind: "project-manager"
    }
}

function _itemSearchText(item) {
    return [item.name, item.comment].concat(item.keywords || [])
        .filter(function(value) { return typeof value === "string" }).join("\n").toLowerCase()
}

function _projectManagerItems(model, query) {
    var normalizedQuery = String(query || "").trim().toLowerCase()
    var candidates = [_projectActionItem("add")]
    if (validateModel(model)) {
        for (var index = 0; index < model.projects.length; index++) {
            var project = model.projects[index]
            var local = project.routes.some(function(route) { return route.isLocal })
            if (local)
                candidates.push(_projectManagerItem(project, index))
        }
    }
    candidates.push(_projectActionItem("manage"))
    var result = candidates.filter(function(item) {
        return !normalizedQuery || _itemSearchText(item).indexOf(normalizedQuery) !== -1
    })
    if (result.length === 0)
        result.push(_statusItem("empty-projects", "No matching projects", "Try another project or catalog action.", 3000))
    return result
}

function isStale(model, now, refreshSeconds) {
    return !validateModel(model) || now - model.generatedAt >= refreshSeconds * 1000
}

function planRunRequest(state, refresh) {
    if (state.active) {
        if (state.settingsGeneration !== state.runSettingsGeneration)
            return { pendingRefresh: state.pendingRefresh, queueRun: true, queueRefresh: state.runWasRefresh || refresh, shouldSchedule: false }
        if (refresh && !state.runWasRefresh)
            return { pendingRefresh: state.pendingRefresh, queueRun: true, queueRefresh: true, shouldSchedule: false }
        return { pendingRefresh: state.pendingRefresh, queueRun: false, queueRefresh: false, shouldSchedule: false }
    }
    return { pendingRefresh: state.pendingRefresh || refresh, queueRun: false,
        queueRefresh: false, shouldSchedule: !state.startScheduled }
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
    if (String(state.category || "") === "projects")
        return _projectManagerItems(model, query)
    if (model === null || model === undefined) {
        if (state.loading)
            return [_statusItem("loading", "Loading Switchboard tasks", "Reading the fleet…", 5000)]
        if (state.failure)
            return [_statusItem("error", "Switchboard fleet unavailable", state.failure.message, 5000)]
        return [_statusItem("initial", "Switchboard has not loaded yet", "A background fleet read will start shortly.", 5000)]
    }
    var result = []
    if (state.failure)
        result.push(_statusItem("degraded-refresh", "Refresh failed — showing last good fleet", state.failure.message, 5000))
    else if (state.loading)
        result.push(_statusItem("refreshing", "Refreshing Switchboard hosts", "Showing the last good fleet while refresh runs.", 5000))
    else if (state.stale)
        result.push(_statusItem("stale", "Switchboard fleet is stale", "Showing retained source-authored state.", 5000))
    var unavailable = model.hosts.filter(function(host) { return host.reachability !== "online" }).length
    if (unavailable > 0)
        result.push(_statusItem("hosts", unavailable + " host" + (unavailable === 1 ? "" : "s") + " unavailable",
            "Retained rows remain inspectable; actions revalidate their owner.", 4900))
    var normalizedQuery = String(query || "").trim().toLowerCase()
    var category = String(state.category || "")
    if (category === "inbox") {
        for (var inboxIndex = 0; inboxIndex < model.inboxSessions.length; inboxIndex++) {
            var session = model.inboxSessions[inboxIndex]
            if (!normalizedQuery || _inboxSearchText(session).indexOf(normalizedQuery) !== -1)
                result.push(_inboxItem(session, state.now, inboxIndex))
        }
    } else {
        for (var taskIndex = 0; taskIndex < model.tasks.length; taskIndex++) {
            var task = model.tasks[taskIndex]
            var categoryMatch = category === "closed" ? task.status === "closed"
                : category.indexOf("project:") === 0 ? task.status === "open"
                    && task.projectId === category.substring(8) : task.status === "open"
            if (categoryMatch && (!normalizedQuery || _taskSearchText(task).indexOf(normalizedQuery) !== -1))
                result.push(_taskItem(task, state.now, taskIndex))
        }
        var creationTitle = String(query || "").trim()
        if (category.indexOf("project:") === 0 && _validTaskTitle(creationTitle)) {
            var projectId = category.substring(8)
            for (var projectIndex = 0; projectIndex < model.projects.length; projectIndex++) {
                var project = model.projects[projectIndex]
                if (project.projectId !== projectId)
                    continue
                var routes = project.routes.filter(function(route) { return route.defaultCheckoutId !== null })
                for (var routeIndex = 0; routeIndex < routes.length; routeIndex++) {
                    result.push(_createItem(project, routes[routeIndex], "codex", creationTitle,
                        routeIndex * 2, routes.length > 1))
                    result.push(_createItem(project, routes[routeIndex], "claude", creationTitle,
                        routeIndex * 2 + 1, routes.length > 1))
                }
                break
            }
        }
        if (!category && model.inboxSessions.length > 0)
            result.push(_statusItem("inbox-summary", "Inbox — " + model.inboxSessions.length
                + " unassigned session" + (model.inboxSessions.length === 1 ? "" : "s"),
                "Choose the Inbox category to review or adopt them.", 1000))
    }
    if (result.length === 0)
        result.push(_statusItem("empty", "No matching Switchboard items", "Try another category or task title.", 3000))
    return result
}
