.pragma library

var BRIDGE_VERSION = 1
var ACTION_VERSION = 1
var MODEL_VERSION = 2
var MAX_EXECUTABLE_LENGTH = 4096

function boundedExecutable(value, fallback) {
    var defaultValue = String(fallback || "swbctl")
    var text = String(value || defaultValue)
    return text.substring(0, MAX_EXECUTABLE_LENGTH)
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

function _validateSession(session, hostId) {
    if (!_object(session) || !_oneOf(session.provider, ["codex", "claude"]))
        return false
    if (!_string(session.sessionKey) || !_string(session.providerSessionId))
        return false
    if (session.hostId !== hostId || !_timestamp(session.recencyAt))
        return false
    if (session.sessionKey !== hostId + ":" + session.provider + ":" + session.providerSessionId)
        return false
    if (!_optionalString(session.name) || !_optionalString(session.cwd))
        return false
    if (!_optionalString(session.projectName) || !_optionalString(session.locationName))
        return false
    if (!_string(session.metadataSource))
        return false
    if (!_oneOf(session.runtimePresence, ["live", "stopped", "unknown"]))
        return false
    if (!_oneOf(session.resumability, ["resumable", "missing", "unknown"]))
        return false
    if (!_oneOf(session.activity, ["working", "needs_input", "ready", "completed", "unknown"]))
        return false
    if (!_oneOf(session.activityReason, ["permission", "question", "elicitation", "turn_complete", "provider_complete", "error", "unknown"]))
        return false
    if (!_oneOf(session.attachment, ["attached", "detached", "none", "unknown"]))
        return false
    if (!_oneOf(session.stateConfidence, ["confirmed", "inferred", "unknown"]))
        return false
    if (typeof session.pinned !== "boolean")
        return false
    return true
}

function _validateLaunchTarget(target) {
    if (!_object(target) || target.provider !== "codex")
        return false
    if (!_string(target.projectId) || !_string(target.projectName))
        return false
    if (!_string(target.locationId) || !_optionalString(target.locationName))
        return false
    return typeof target.isDefault === "boolean"
}

function _validateCapability(capability, provider) {
    if (!_object(capability) || capability.provider !== provider)
        return false
    if (["available", "degraded", "neutral"].indexOf(capability.status) === -1)
        return false
    if (capability.available !== null && typeof capability.available !== "boolean")
        return false
    if (!Array.isArray(capability.features) || !Array.isArray(capability.degradedReasons))
        return false
    if (capability.status === "neutral" && capability.available !== null)
        return false
    if (capability.status === "available" && capability.available !== true)
        return false
    return true
}

function validateModel(model) {
    if (!_object(model) || model.modelVersion !== MODEL_VERSION)
        return false
    if (!_timestamp(model.generatedAt) || !_object(model.host))
        return false
    if (!_string(model.host.hostId) || !_string(model.host.displayName))
        return false
    if (!Array.isArray(model.sessions) || !Array.isArray(model.launchTargets) || !Array.isArray(model.capabilities) || !Array.isArray(model.warnings))
        return false
    if (model.capabilities.length !== 2 || !_validateCapability(model.capabilities[0], "codex") || !_validateCapability(model.capabilities[1], "claude"))
        return false

    var identities = {}
    for (var index = 0; index < model.sessions.length; index++) {
        var session = model.sessions[index]
        if (!_validateSession(session, model.host.hostId) || identities[session.sessionKey] === true)
            return false
        identities[session.sessionKey] = true
    }
    var locations = {}
    for (var targetIndex = 0; targetIndex < model.launchTargets.length; targetIndex++) {
        var target = model.launchTargets[targetIndex]
        if (!_validateLaunchTarget(target) || locations[target.locationId] === true)
            return false
        locations[target.locationId] = true
    }
    return true
}

function parseBridgeResponse(text) {
    var envelope
    try {
        envelope = JSON.parse(String(text))
    } catch (error) {
        return _failure(
            "bridge_invalid_json",
            "The bridge returned an invalid response.",
            false
        )
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
    if (["focused", "switched", "launched"].indexOf(envelope.action.kind) === -1 || !_string(envelope.action.surfaceId))
        return _failure("action_invalid_result", "The session opener returned an invalid result.", false)
    return { ok: true, action: envelope.action }
}

function _compareSessions(left, right) {
    if (left.recencyAt !== right.recencyAt)
        return right.recencyAt - left.recencyAt
    if (left.sessionKey < right.sessionKey)
        return -1
    if (left.sessionKey > right.sessionKey)
        return 1
    return 0
}

function _basename(path) {
    if (!_string(path))
        return ""
    var trimmed = path.replace(/\/+$/, "")
    var separator = trimmed.lastIndexOf("/")
    return separator === -1 ? trimmed : trimmed.substring(separator + 1)
}

function _displayName(session) {
    if (_string(session.name))
        return session.name
    if (_string(session.projectName))
        return session.projectName
    var directory = _basename(session.cwd)
    if (directory)
        return directory
    return (session.provider === "claude" ? "Claude " : "Codex ") + session.providerSessionId.substring(0, 8)
}

function _age(timestamp, now) {
    var seconds = Math.max(0, Math.floor((now - timestamp) / 1000))
    if (seconds < 60)
        return "now"
    var minutes = Math.floor(seconds / 60)
    if (minutes < 60)
        return String(minutes) + "m ago"
    var hours = Math.floor(minutes / 60)
    if (hours < 24)
        return String(hours) + "h ago"
    var days = Math.floor(hours / 24)
    return String(days) + "d ago"
}

function _sourceState(session) {
    var values = []
    if (session.activity !== "unknown")
        values.push("activity " + session.activity)
    if (session.runtimePresence !== "unknown")
        values.push("runtime " + session.runtimePresence)
    if (session.resumability !== "unknown")
        values.push("resume " + session.resumability)
    if (session.attachment !== "unknown")
        values.push("attachment " + session.attachment)
    return values.length > 0 ? values.join(" · ") : "state unknown"
}

function _searchText(session, host) {
    return [
        session.name,
        session.cwd,
        session.projectName,
        session.locationName,
        session.sessionKey,
        session.providerSessionId,
        session.provider,
        host.displayName,
        host.hostId
    ].filter(function(value) {
        return typeof value === "string"
    }).join("\n").toLowerCase()
}

function _sessionItem(session, host, now, index) {
    var commentParts = []
    if (_string(session.cwd))
        commentParts.push(session.cwd)
    if (_string(session.projectName))
        commentParts.push("project " + session.projectName)
    if (_string(session.locationName))
        commentParts.push("location " + session.locationName)
    commentParts.push(session.provider === "claude" ? "Claude" : "Codex")
    commentParts.push(_age(session.recencyAt, now))
    commentParts.push(_sourceState(session))
    return {
        id: "switchboard:session:" + session.sessionKey,
        name: _displayName(session),
        icon: "material:terminal",
        comment: commentParts.join(" | "),
        categories: ["Switchboard"],
        keywords: [session.sessionKey, session.providerSessionId],
        _preScored: 3000 - index,
        _switchboardKind: "session",
        _sessionKey: session.sessionKey,
        _windowHost: host.displayName
    }
}

function _launchTargetSearchText(target, host) {
    return [
        target.projectName,
        target.locationName,
        target.projectId,
        target.locationId,
        host.displayName,
        host.hostId,
        "new codex session"
    ].filter(function(value) {
        return typeof value === "string"
    }).join("\n").toLowerCase()
}

function _launchTargetItem(target, host, projectTargetCount, index) {
    var label = target.projectName
    if (projectTargetCount > 1 && !target.isDefault) {
        var locationLabel = _string(target.locationName)
            ? target.locationName
            : target.locationId.substring(0, 8)
        label += " — " + locationLabel
    }
    var comment = target.isDefault
        ? "Start a new Codex session in the default project location."
        : "Start a new Codex session in this configured project location."
    return {
        id: "switchboard:new:" + target.projectId + ":" + target.locationId,
        name: "New Codex — " + label,
        icon: "material:add_to_terminal",
        comment: comment,
        categories: ["Switchboard"],
        keywords: [target.projectId, target.locationId],
        _preScored: 4000 - index,
        _switchboardKind: "new",
        _projectId: target.projectId,
        _locationId: target.locationId,
        _windowHost: host.displayName
    }
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

function _degradedComment(capability) {
    var reasons = capability.degradedReasons
    var codes = []
    for (var index = 0; index < reasons.length; index++) {
        if (_object(reasons[index]) && _string(reasons[index].code))
            codes.push(reasons[index].code)
    }
    var providerName = capability.provider === "claude" ? "Claude" : "Codex"
    return codes.length > 0 ? codes.join(", ") : "The source reported degraded " + providerName + " capability."
}

function isStale(model, now, refreshSeconds) {
    if (!validateModel(model))
        return true
    return now - model.generatedAt >= refreshSeconds * 1000
}

function planRunRequest(state, refresh) {
    if (state.active) {
        if (state.settingsGeneration !== state.runSettingsGeneration) {
            return {
                pendingRefresh: state.pendingRefresh,
                queueRun: true,
                queueRefresh: state.runWasRefresh || refresh,
                shouldSchedule: false
            }
        }
        if (refresh && !state.runWasRefresh) {
            return {
                pendingRefresh: state.pendingRefresh,
                queueRun: true,
                queueRefresh: true,
                shouldSchedule: false
            }
        }
        return {
            pendingRefresh: state.pendingRefresh,
            queueRun: false,
            queueRefresh: false,
            shouldSchedule: false
        }
    }
    return {
        pendingRefresh: state.pendingRefresh || refresh,
        queueRun: false,
        queueRefresh: false,
        shouldSchedule: !state.startScheduled
    }
}

function stoppedRunDisposition(state, deadline) {
    if (!state.runActive || state.running)
        return "none"
    if (state.observedRunGeneration !== state.runGeneration)
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
            return [_statusItem("loading", "Loading Switchboard sessions", "Reading a validated local snapshot…", 5000)]
        if (state.failure)
            return [_statusItem("error", "Switchboard snapshot unavailable", state.failure.message, 5000)]
        return [_statusItem("initial", "Switchboard has not loaded yet", "A background snapshot read will start shortly.", 5000)]
    }

    var result = []
    if (state.failure) {
        result.push(_statusItem(
            "degraded-refresh",
            "Refresh failed — showing last good snapshot",
            state.failure.message,
            5000
        ))
    } else if (state.loading) {
        result.push(_statusItem("refreshing", "Refreshing Switchboard sessions", "Showing the last good snapshot while refresh runs.", 5000))
    } else if (state.stale) {
        result.push(_statusItem("stale", "Switchboard snapshot is stale", "Source-authored state is shown without added liveness or activity guesses.", 5000))
    }

    for (var capabilityIndex = 0; capabilityIndex < model.capabilities.length; capabilityIndex++) {
        var capability = model.capabilities[capabilityIndex]
        var providerName = capability.provider === "claude" ? "Claude" : "Codex"
        var statusKind = capability.provider === "codex" ? "capability" : "claude-capability"
        if (capability.status === "degraded") {
            result.push(_statusItem(statusKind + "-degraded", providerName + " data is degraded", _degradedComment(capability), 4900 - capabilityIndex))
        } else if (capability.status === "neutral") {
            result.push(_statusItem(statusKind + "-neutral", providerName + " capability is unknown", "The retained snapshot did not report a " + providerName + " capability.", 4900 - capabilityIndex))
        }
    }

    var normalizedQuery = String(query || "").trim().toLowerCase()
    var targetCounts = {}
    for (var targetCountIndex = 0; targetCountIndex < model.launchTargets.length; targetCountIndex++) {
        var projectId = model.launchTargets[targetCountIndex].projectId
        targetCounts[projectId] = (targetCounts[projectId] || 0) + 1
    }
    var matchedTargets = []
    for (var targetIndex = 0; targetIndex < model.launchTargets.length; targetIndex++) {
        if (!normalizedQuery || _launchTargetSearchText(model.launchTargets[targetIndex], model.host).indexOf(normalizedQuery) !== -1)
            matchedTargets.push(model.launchTargets[targetIndex])
    }
    for (var launchIndex = 0; launchIndex < matchedTargets.length; launchIndex++) {
        var launchTarget = matchedTargets[launchIndex]
        result.push(_launchTargetItem(launchTarget, model.host, targetCounts[launchTarget.projectId], launchIndex))
    }

    var sessions = model.sessions.slice().sort(_compareSessions)
    var matched = []
    for (var index = 0; index < sessions.length; index++) {
        if (!normalizedQuery || _searchText(sessions[index], model.host).indexOf(normalizedQuery) !== -1)
            matched.push(sessions[index])
    }
    for (var itemIndex = 0; itemIndex < matched.length; itemIndex++)
        result.push(_sessionItem(matched[itemIndex], model.host, now, itemIndex))

    if (model.sessions.length === 0 && model.launchTargets.length === 0)
        result.push(_statusItem("empty", "No local sessions or projects", "The validated snapshot contains no local sessions or launch targets.", 3000))
    else if (matched.length === 0 && matchedTargets.length === 0)
        result.push(_statusItem("no-match", "No matching Switchboard items", "Search covers sessions, projects, locations, hosts, and stable identities.", 3000))
    return result
}
