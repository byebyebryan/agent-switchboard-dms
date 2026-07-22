.pragma library

var ADAPTER_VERSION = "0.5.0"
var VALIDATION_VERSION = 1
var MAX_EXECUTABLE_LENGTH = 4096

function _object(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value)
}

function _string(value) {
    return typeof value === "string" && value.length > 0
        && value.length <= 65536 && value.indexOf("\u0000") === -1
}

function _uuid(value) {
    return _string(value) && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)
}

function _integer(value) {
    return typeof value === "number" && isFinite(value) && Math.floor(value) === value && value >= 0
}

function _array(value, maximum) {
    return Array.isArray(value) && value.length <= maximum
}

function boundedExecutable(value, fallback) {
    var text = typeof value === "string" ? value : String(fallback || "swbctl")
    if (!text || text.length > MAX_EXECUTABLE_LENGTH || /[\u0000-\u001f\u007f]/.test(text))
        return String(fallback || "swbctl")
    return text
}

function validateModel(model) {
    if (!_object(model) || model.modelVersion !== 1 || model.sourceNavigatorVersion !== 1
            || !_uuid(model.sourceGenerationId) || !_uuid(model.localHostId)
            || !_integer(model.generatedAt) || !_array(model.hosts, 20000)
            || !_array(model.views, 20000) || !_array(model.projects, 20000)
            || !_array(model.recoveries, 20000) || !_array(model.warnings, 20000)
            || !_object(model.truncation))
        return false
    var hosts = {}
    var localCount = 0
    for (var hostIndex = 0; hostIndex < model.hosts.length; hostIndex++) {
        var host = model.hosts[hostIndex]
        if (!_object(host) || !_uuid(host.hostId) || !_uuid(host.generationId)
                || !_string(host.displayName) || typeof host.isLocal !== "boolean"
                || ["online", "offline", "unknown"].indexOf(host.reachability) === -1
                || typeof host.stale !== "boolean" || !_integer(host.generatedAt)
                || ["cutover_staged", "committed"].indexOf(host.activationState) === -1
                || hosts[host.hostId] !== undefined)
            return false
        hosts[host.hostId] = host
        if (host.isLocal)
            localCount++
    }
    if (localCount !== 1 || !hosts[model.localHostId] || !hosts[model.localHostId].isLocal
            || hosts[model.localHostId].generationId !== model.sourceGenerationId)
        return false
    var views = {}
    for (var viewIndex = 0; viewIndex < model.views.length; viewIndex++) {
        var view = model.views[viewIndex]
        if (!_object(view) || !hosts[view.hostId] || !_uuid(view.viewId)
                || ["navigator", "direct"].indexOf(view.mode) === -1
                || ["ready", "transitioning", "degraded", "retired"].indexOf(view.state) === -1
                || !_integer(view.revision) || !_string(view.title)
                || !_array(view.breadcrumb, 32) || !_string(view.activity)
                || !_string(view.attention) || views[view.hostId + ":" + view.viewId])
            return false
        for (var crumbIndex = 0; crumbIndex < view.breadcrumb.length; crumbIndex++)
            if (!_string(view.breadcrumb[crumbIndex]))
                return false
        views[view.hostId + ":" + view.viewId] = true
    }
    for (var projectIndex = 0; projectIndex < model.projects.length; projectIndex++) {
        var project = model.projects[projectIndex]
        if (!_object(project) || !hosts[project.hostId] || !_uuid(project.projectId)
                || !_string(project.name) || (project.viewId !== null && !_uuid(project.viewId))
                || (project.entryFrameId !== null && !_uuid(project.entryFrameId))
                || !_array(project.frames, 20000)
                || (project.viewId !== null && !views[project.hostId + ":" + project.viewId]))
            return false
    }
    for (var recoveryIndex = 0; recoveryIndex < model.recoveries.length; recoveryIndex++) {
        var recovery = model.recoveries[recoveryIndex]
        if (!_object(recovery) || !hosts[recovery.hostId] || !_uuid(recovery.recoveryId)
                || !_string(recovery.kind) || !_string(recovery.explanation)
                || ["safe_auto", "open_view", "manual"].indexOf(recovery.actionability) === -1
                || recovery.state !== "open")
            return false
    }
    return true
}

function cacheEnvelope(model) {
    if (!validateModel(model))
        return null
    return {
        adapterVersion: ADAPTER_VERSION,
        validationVersion: VALIDATION_VERSION,
        sourceGenerationId: model.sourceGenerationId,
        generatedAt: model.generatedAt,
        model: model
    }
}

function cachedModel(envelope) {
    if (!_object(envelope) || envelope.adapterVersion !== ADAPTER_VERSION
            || envelope.validationVersion !== VALIDATION_VERSION
            || !_uuid(envelope.sourceGenerationId) || !_integer(envelope.generatedAt)
            || !validateModel(envelope.model)
            || envelope.sourceGenerationId !== envelope.model.sourceGenerationId
            || envelope.generatedAt !== envelope.model.generatedAt)
        return null
    return envelope.model
}

function isStale(model, now, refreshSeconds) {
    return !validateModel(model) || now - model.generatedAt >= refreshSeconds * 1000
}

function launcherCategories(model) {
    var result = [{ id: "", name: "Views" }, { id: "projects", name: "Projects" }]
    if (validateModel(model) && model.recoveries.length > 0)
        result.push({ id: "recovery", name: "Recovery" })
    return result
}

function _status(kind, name, comment, score) {
    return { id: "switchboard:status:" + kind, name: name, icon: "material:info",
        comment: comment, categories: ["Switchboard"], _preScored: score,
        _switchboardKind: "status" }
}

function _hostMap(model) {
    var result = {}
    for (var index = 0; index < model.hosts.length; index++)
        result[model.hosts[index].hostId] = model.hosts[index]
    return result
}

function _matches(item, query) {
    var normalized = String(query || "").trim().toLowerCase()
    if (!normalized)
        return true
    return [item.name, item.comment].concat(item.keywords || []).join("\n").toLowerCase().indexOf(normalized) !== -1
}

function _hostSuffix(host) {
    if (host.isLocal)
        return host.displayName
    return host.displayName + (host.reachability === "online" ? "" : " · " + host.reachability)
}

function _viewItem(view, host, index) {
    var details = []
    if (view.breadcrumb.length > 0)
        details.push(view.breadcrumb.join(" › "))
    details.push(_hostSuffix(host))
    details.push(view.mode === "direct" ? "Direct" : "Navigator")
    details.push(view.attention !== "none" ? view.attention : view.activity)
    if (host.stale)
        details.push("Stale")
    return {
        id: "switchboard:view:" + view.hostId + ":" + view.viewId,
        name: view.title,
        icon: view.attention === "recovery" ? "material:warning" : "material:terminal",
        badgeLabel: "View",
        comment: details.join(" | "),
        categories: ["Switchboard"],
        keywords: view.breadcrumb.concat([host.displayName, view.mode, view.state, view.activity]),
        _preScored: 5000 - index,
        _switchboardKind: "view",
        _hostId: view.hostId,
        _targetId: view.viewId
    }
}

function _projectItem(project, host, index) {
    var available = project.entryFrameId !== null
    return {
        id: "switchboard:project:" + project.hostId + ":" + project.projectId,
        name: project.name,
        icon: available ? "material:folder_code" : "material:folder_off",
        badgeLabel: "Project",
        comment: _hostSuffix(host) + " | " + (available ? "Open workspace view" : "Needs structural recovery"),
        categories: ["Switchboard"],
        keywords: [host.displayName],
        _preScored: 4000 - index,
        _switchboardKind: "project",
        _hostId: project.hostId,
        _targetId: project.projectId
    }
}

function _recoveryItem(recovery, host, index) {
    return {
        id: "switchboard:recovery:" + recovery.hostId + ":" + recovery.recoveryId,
        name: recovery.kind.replace(/_/g, " "),
        icon: "material:warning",
        badgeLabel: "Recovery",
        comment: _hostSuffix(host) + " | " + recovery.explanation,
        categories: ["Switchboard"],
        keywords: [host.displayName, recovery.kind, recovery.actionability, recovery.explanation],
        _preScored: 6000 - index,
        _switchboardKind: "recovery",
        _hostId: recovery.hostId,
        _targetId: recovery.recoveryId,
        _actionability: recovery.actionability
    }
}

function launcherItems(model, query, state) {
    if (!validateModel(model)) {
        if (state.loading)
            return [_status("loading", "Loading Switchboard views", "Reading NavigatorState v1…", 5000)]
        if (state.failure)
            return [_status("error", "Switchboard unavailable", state.failure.message, 5000)]
        return [_status("empty", "Switchboard has not loaded", "A background read will start shortly.", 5000)]
    }
    var result = []
    if (state.failure)
        result.push(_status("retained", "Refresh failed — retained entries are read-only", state.failure.message, 7000))
    else if (state.loading)
        result.push(_status("refreshing", "Refreshing Switchboard views", "Showing the last-good entry model.", 7000))
    else if (!state.fresh)
        result.push(_status("cold-cache", "Cached entries require a fresh read", "Selection is disabled until generation provenance is refreshed.", 7000))
    else if (state.stale)
        result.push(_status("stale", "Switchboard state is stale", "Actions still revalidate on the owner host.", 7000))
    var hosts = _hostMap(model)
    var category = String(state.category || "")
    var rows = category === "projects" ? model.projects
        : category === "recovery" ? model.recoveries : model.views
    for (var index = 0; index < rows.length; index++) {
        var row = rows[index]
        var item = category === "projects" ? _projectItem(row, hosts[row.hostId], index)
            : category === "recovery" ? _recoveryItem(row, hosts[row.hostId], index)
                : _viewItem(row, hosts[row.hostId], index)
        if (_matches(item, query))
            result.push(item)
    }
    if (result.length === 0)
        result.push(_status("no-match", "No matching Switchboard entries", "Try another visible title or category.", 3000))
    return result
}

function parseActionResponse(text) {
    var envelope
    try {
        envelope = JSON.parse(String(text))
    } catch (error) {
        return { ok: false, error: { code: "action_invalid_json", message: "The desktop helper returned invalid JSON.", retryable: false } }
    }
    if (!_object(envelope) || envelope.actionVersion !== 1 || typeof envelope.ok !== "boolean")
        return { ok: false, error: { code: "action_incompatible", message: "The desktop helper is incompatible.", retryable: false } }
    if (!envelope.ok)
        return _object(envelope.error) && _string(envelope.error.code) && _string(envelope.error.message)
            ? { ok: false, error: envelope.error }
            : { ok: false, error: { code: "action_invalid_error", message: "The desktop helper returned an invalid error.", retryable: false } }
    if (!_object(envelope.action) || ["focused", "launched"].indexOf(envelope.action.kind) === -1
            || !_uuid(envelope.action.hostId) || !_uuid(envelope.action.viewId)
            || !_uuid(envelope.action.requestId))
        return { ok: false, error: { code: "action_invalid_result", message: "The desktop helper returned an invalid result.", retryable: false } }
    return { ok: true, action: envelope.action }
}
