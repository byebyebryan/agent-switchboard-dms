.pragma library

var BRIDGE_VERSION = 1
var MODEL_VERSION = 1
var MAX_EXECUTABLE_LENGTH = 4096

function boundedExecutable(value) {
    var text = String(value || "swbctl")
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
    if (!_object(session) || session.provider !== "codex")
        return false
    if (!_string(session.sessionKey) || !_string(session.providerSessionId))
        return false
    if (session.hostId !== hostId || !_timestamp(session.recencyAt))
        return false
    if (session.sessionKey !== hostId + ":codex:" + session.providerSessionId)
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

function _validateCapability(capability) {
    if (!_object(capability) || capability.provider !== "codex")
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
    if (!Array.isArray(model.sessions) || !Array.isArray(model.warnings))
        return false
    if (!_validateCapability(model.codexCapability))
        return false

    var identities = {}
    for (var index = 0; index < model.sessions.length; index++) {
        var session = model.sessions[index]
        if (!_validateSession(session, model.host.hostId) || identities[session.sessionKey] === true)
            return false
        identities[session.sessionKey] = true
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
    return "Codex " + session.providerSessionId.substring(0, 8)
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
        _sessionKey: session.sessionKey
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

function _degradedComment(model) {
    var reasons = model.codexCapability.degradedReasons
    var codes = []
    for (var index = 0; index < reasons.length; index++) {
        if (_object(reasons[index]) && _string(reasons[index].code))
            codes.push(reasons[index].code)
    }
    return codes.length > 0 ? codes.join(", ") : "The source reported degraded Codex capability."
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

    if (model.codexCapability.status === "degraded") {
        result.push(_statusItem("capability-degraded", "Codex data is degraded", _degradedComment(model), 4900))
    } else if (model.codexCapability.status === "neutral") {
        result.push(_statusItem("capability-neutral", "Codex capability is unknown", "The retained snapshot did not report a Codex capability.", 4900))
    }

    var normalizedQuery = String(query || "").trim().toLowerCase()
    var sessions = model.sessions.slice().sort(_compareSessions)
    var matched = []
    for (var index = 0; index < sessions.length; index++) {
        if (!normalizedQuery || _searchText(sessions[index], model.host).indexOf(normalizedQuery) !== -1)
            matched.push(sessions[index])
    }
    for (var itemIndex = 0; itemIndex < matched.length; itemIndex++)
        result.push(_sessionItem(matched[itemIndex], model.host, now, itemIndex))

    if (model.sessions.length === 0)
        result.push(_statusItem("empty", "No local Codex sessions", "The validated snapshot contains no Codex sessions.", 3000))
    else if (matched.length === 0)
        result.push(_statusItem("no-match", "No matching Switchboard sessions", "Search covers name, path, project, location, host, and session identity.", 3000))
    return result
}
