import QtQuick
import Quickshell
import Quickshell.Io
import "SwitchboardModelV4Projects.js" as SwitchboardModelV4
import qs.Common

Item {
    id: root

    readonly property string pluginName: "switchboard"
    readonly property string modelStateKey: "last_good_model_v4_bridge3"
    readonly property int bridgeContractVersion: 3
    readonly property int modelContractVersion: 4
    readonly property string bridgeExecutable: Paths.strip(Qt.resolvedUrl("switchboard-bridge"))
    readonly property string openerExecutable: Paths.strip(Qt.resolvedUrl("switchboard-open"))
    readonly property string projectManagerExecutable: Paths.strip(Qt.resolvedUrl("switchboard-projects"))
    property var pluginService: null
    property string trigger: "sb:"
    property string swbctlExecutable: "swbctl"
    property string terminalExecutable: "ghostty"
    property string activeCategory: ""
    property int timeoutMs: 10000
    property int refreshSeconds: 15
    property var lastGoodModel: null
    property var currentFailure: null
    property bool runActive: false
    property bool runExpired: false
    property bool runWasRefresh: false
    property bool startScheduled: false
    property bool pendingRefresh: false
    property bool queuedRun: false
    property bool queuedRefresh: false
    property bool stdoutFinished: false
    property bool stderrFinished: false
    property bool exitFinished: false
    property int runExitCode: -1
    property string runStdout: ""
    property int settingsGeneration: 0
    property int runSettingsGeneration: -1
    property int runGeneration: 0
    property int automaticRetryBudget: 1
    property bool cacheLoadedFromState: false
    property string cachedModelFingerprint: ""
    property bool actionActive: false
    property bool actionExpired: false
    property bool actionStdoutFinished: false
    property bool actionStderrFinished: false
    property bool actionExitFinished: false
    property int actionExitCode: -1
    property string actionStdout: ""
    property bool managerActive: false
    property bool managerStartFailed: false
    property bool managerStdoutFinished: false
    property bool managerStderrFinished: false
    property bool managerExitFinished: false
    property int managerExitCode: -1
    property string managerStdout: ""

    signal itemsChanged

    function boundedInteger(value, minimum, maximum, fallback) {
        const parsed = parseInt(value);
        if (isNaN(parsed))
            return fallback;

        return Math.max(minimum, Math.min(maximum, parsed));
    }

    function modelObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function modelString(value) {
        return typeof value === "string" && value.length > 0;
    }

    function validateFrontendModel(model) {
        return SwitchboardModelV4.validateModel(model);
    }

    function bridgeFailure(code, message, retryable) {
        return {
            "ok": false,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable === true
            }
        };
    }

    function parseCurrentBridgeResponse(text) {
        let envelope;
        try {
            envelope = JSON.parse(String(text));
        } catch (error) {
            return bridgeFailure("bridge_invalid_json", "The bridge returned an invalid response.", false);
        }
        if (!modelObject(envelope) || envelope.bridgeVersion !== bridgeContractVersion)
            return bridgeFailure("bridge_incompatible", "The bridge response is incompatible.", false);
        if (envelope.ok === false) {
            if (!modelObject(envelope.error) || !modelString(envelope.error.code) || !modelString(envelope.error.message))
                return bridgeFailure("bridge_invalid_error", "The bridge returned an invalid error.", false);
            return bridgeFailure(envelope.error.code, envelope.error.message, envelope.error.retryable);
        }
        if (envelope.ok !== true || !validateFrontendModel(envelope.model))
            return bridgeFailure("bridge_invalid_model", "The bridge returned an invalid model.", false);
        return {
            "ok": true,
            "model": envelope.model
        };
    }

    function persistentModelFingerprint(model) {
        if (!validateFrontendModel(model))
            return "";
        return JSON.stringify({
            "modelVersion": model.modelVersion,
            "sourceSchemaVersion": model.sourceSchemaVersion,
            "sourceProtocolVersion": model.sourceProtocolVersion,
            "sourceFleetVersion": model.sourceFleetVersion,
            "localHostId": model.localHostId,
            "hosts": model.hosts,
            "projects": model.projects,
            "tasks": model.tasks,
            "inboxSessions": model.inboxSessions,
            "warnings": model.warnings,
            "truncation": model.truncation
        });
    }

    function loadSettings() {
        if (!pluginService)
            return;

        const configuredExecutable = pluginService.loadPluginData(pluginName, "swbctl", "swbctl");
        const nextExecutable = SwitchboardModelV4.boundedExecutable(configuredExecutable);
        const configuredTerminal = pluginService.loadPluginData(pluginName, "terminal", "ghostty");
        const nextTerminal = SwitchboardModelV4.boundedExecutable(configuredTerminal, "ghostty");
        const nextTimeout = boundedInteger(pluginService.loadPluginData(pluginName, "timeout_ms", 10000), 100, 60000, 10000);
        const nextRefresh = boundedInteger(pluginService.loadPluginData(pluginName, "refresh_seconds", 15), 5, 300, 15);
        const changed = nextExecutable !== swbctlExecutable || nextTerminal !== terminalExecutable || nextTimeout !== timeoutMs || nextRefresh !== refreshSeconds;
        swbctlExecutable = nextExecutable;
        terminalExecutable = nextTerminal;
        timeoutMs = nextTimeout;
        refreshSeconds = nextRefresh;
        if (changed) {
            settingsGeneration += 1;
            automaticRetryBudget = 1;
        }
    }

    function loadCachedModel() {
        if (!pluginService || typeof pluginService.loadPluginState !== "function")
            return;

        try {
            const cached = pluginService.loadPluginState(pluginName, modelStateKey, null);
            if (validateFrontendModel(cached)) {
                lastGoodModel = cached;
                cacheLoadedFromState = true;
                cachedModelFingerprint = persistentModelFingerprint(cached);
            }
        } catch (error) {
            console.warn("Switchboard cached model read failed");
        }
    }

    function saveCachedModel(model, requireFollowup) {
        if (!pluginService || typeof pluginService.savePluginState !== "function")
            return;

        try {
            pluginService.savePluginState(pluginName, modelStateKey, model);
            cachedModelFingerprint = persistentModelFingerprint(model);
            if (!cacheLoadedFromState || requireFollowup === true)
                cacheWriteFollowup.restart();
        } catch (error) {
            console.warn("Switchboard cached model write failed");
        }
    }

    function fleetIsStale(now) {
        return lastGoodModel !== null && SwitchboardModelV4.isStale(lastGoodModel, now, refreshSeconds);
    }

    function scheduleForRead() {
        const now = Date.now();
        if (lastGoodModel === null) {
            scheduleRun(false);
            return;
        }
        if (fleetIsStale(now))
            scheduleRun(true);
    }

    function getItems(query) {
        Qt.callLater(root.scheduleForRead);
        const now = Date.now();
        return SwitchboardModelV4.launcherItems(lastGoodModel, query, {
            "now": now,
            "loading": runActive || startScheduled,
            "stale": fleetIsStale(now),
            "failure": currentFailure,
            "category": activeCategory
        });
    }

    function getCategories() {
        return SwitchboardModelV4.launcherCategories(lastGoodModel);
    }

    function setCategory(categoryId) {
        activeCategory = String(categoryId || "");
    }

    function executeItem(item) {
        if (!item)
            return;
        if (item._switchboardKind === "project-add" || item._switchboardKind === "project-manager") {
            startProjectManager(item);
            return;
        }
        if (actionActive || managerActive || !item._windowHost || !item._hostId)
            return;

        let targetArguments;
        if (item._switchboardKind === "task" && item._taskId)
            targetArguments = ["--task", item._taskId];
        else if (item._switchboardKind === "create" && item._projectId && item._checkoutId && item._provider && item._title)
            targetArguments = ["--create", "--project", item._projectId, "--title", item._title, "--checkout", item._checkoutId, "--provider", item._provider];
        else if (item._switchboardKind === "session" && item._sessionKey)
            targetArguments = [item._sessionKey];
        else
            return;
        startAction(item, targetArguments);
    }

    function startProjectManager(item) {
        if (managerActive || actionActive || !item)
            return;

        const command = [projectManagerExecutable, "--swbctl", swbctlExecutable, "--terminal", terminalExecutable, "--timeout-ms", String(timeoutMs)];
        if (item._switchboardKind === "project-add")
            command.push("--add-project");
        else if (item._switchboardKind === "project-manager" && item._projectId)
            command.push("--project", item._projectId);
        else if (item._switchboardKind !== "project-manager")
            return;

        managerActive = true;
        managerStartFailed = false;
        managerStdoutFinished = false;
        managerStderrFinished = false;
        managerExitFinished = false;
        managerExitCode = -1;
        managerStdout = "";
        managerProcess.command = command;
        managerProcess.running = true;
        itemsChanged();
    }

    function scheduleStoppedManagerCheck() {
        Qt.callLater(() => {
            Qt.callLater(root.finishStoppedManagerIfNeeded);
        });
    }

    function finishStoppedManagerIfNeeded() {
        if (!managerActive || managerProcess.running || managerExitFinished)
            return;

        managerStdoutFinished = true;
        managerStderrFinished = true;
        managerExitFinished = true;
        managerStartFailed = true;
        setFailure("project_manager_start_failed", "The project manager process could not be started.", true);
        maybeFinishManager();
    }

    function maybeFinishManager() {
        if (!managerActive || !managerStdoutFinished || !managerStderrFinished || !managerExitFinished)
            return;

        if (managerStartFailed) {
            managerActive = false;
            itemsChanged();
            return;
        }

        const parsed = parseCurrentBridgeResponse(managerStdout);
        if (managerExitCode === 0 && parsed.ok) {
            lastGoodModel = parsed.model;
            saveCachedModel(parsed.model, true);
            currentFailure = null;
            automaticRetryBudget = 1;
        } else if (!parsed.ok) {
            setFailure(parsed.error.code, parsed.error.message, parsed.error.retryable);
        } else {
            setFailure("project_manager_exit_mismatch", "The project manager exited unsuccessfully after refreshing the catalog.", true);
        }
        managerActive = false;
        itemsChanged();
    }

    function getContextMenuActions(item) {
        if (!item || !item._windowHost || !item._hostId)
            return [];

        const result = [];
        if (item._projectId && item._checkoutId)
            result.push({
                "text": "Claude history",
                "icon": "history",
                "closeLauncher": true,
                "action": function () {
                    root.startAction(item, ["--history", "--project", item._projectId, "--checkout", item._checkoutId]);
                }
            });

        if (item._provider === "claude" && item._canStop && item._sessionKey)
            result.push({
                "text": "Stop Claude runtime",
                "icon": "stop_circle",
                "closeLauncher": true,
                "action": function () {
                    root.startAction(item, ["--stop", item._sessionKey]);
                }
            });

        return result;
    }

    function startAction(item, targetArguments) {
        if (actionActive || !item || !item._windowHost || !item._hostId || !Array.isArray(targetArguments))
            return;

        actionActive = true;
        actionExpired = false;
        actionStdoutFinished = false;
        actionStderrFinished = false;
        actionExitFinished = false;
        actionExitCode = -1;
        actionStdout = "";
        actionProcess.command = [openerExecutable, "--swbctl", swbctlExecutable, "--terminal", terminalExecutable, "--timeout-ms", String(timeoutMs), "--window-host", item._windowHost, "--host", item._hostId].concat(targetArguments);
        actionDeadline.interval = timeoutMs * 4 + 5000;
        actionDeadline.restart();
        actionProcess.running = true;
        itemsChanged();
    }

    function scheduleStoppedActionCheck() {
        Qt.callLater(() => {
            Qt.callLater(root.finishStoppedActionIfNeeded);
        });
    }

    function finishStoppedActionIfNeeded() {
        if (!actionActive || actionProcess.running || actionExitFinished)
            return;

        actionExpired = true;
        actionStdoutFinished = true;
        actionStderrFinished = true;
        actionExitFinished = true;
        setFailure("action_start_failed", "The session opener process could not be started.", true);
        maybeFinishAction();
    }

    function maybeFinishAction() {
        if (!actionActive || !actionStdoutFinished || !actionStderrFinished || !actionExitFinished)
            return;

        actionDeadline.stop();
        if (!actionExpired) {
            const parsed = SwitchboardModelV4.parseActionResponse(actionStdout);
            if (actionExitCode === 0 && parsed.ok) {
                currentFailure = null;
                scheduleRun(true);
            } else if (!parsed.ok) {
                setFailure(parsed.error.code, parsed.error.message, parsed.error.retryable);
            } else {
                setFailure("action_exit_mismatch", "The session opener exited unsuccessfully after returning a result.", true);
            }
        }
        actionActive = false;
        itemsChanged();
    }

    function scheduleRun(refresh) {
        const plan = SwitchboardModelV4.planRunRequest({
            "active": runActive || refreshProcess.running,
            "runWasRefresh": runWasRefresh,
            "settingsGeneration": settingsGeneration,
            "runSettingsGeneration": runSettingsGeneration,
            "pendingRefresh": pendingRefresh,
            "startScheduled": startScheduled
        }, refresh);
        pendingRefresh = plan.pendingRefresh;
        if (plan.queueRun) {
            queuedRun = true;
            queuedRefresh = queuedRefresh || plan.queueRefresh;
        }
        if (!plan.shouldSchedule)
            return;

        startScheduled = true;
        Qt.callLater(root.startPendingRun);
    }

    function startPendingRun() {
        if (!startScheduled)
            return;

        startScheduled = false;
        if (runActive || refreshProcess.running) {
            queuedRun = true;
            queuedRefresh = queuedRefresh || pendingRefresh;
            pendingRefresh = false;
            return;
        }
        const refresh = pendingRefresh;
        pendingRefresh = false;
        startRun(refresh);
    }

    function startRun(refresh) {
        runGeneration += 1;
        runActive = true;
        runExpired = false;
        runWasRefresh = refresh;
        runSettingsGeneration = settingsGeneration;
        stdoutFinished = false;
        stderrFinished = false;
        exitFinished = false;
        runExitCode = -1;
        runStdout = "";
        const command = [bridgeExecutable, "--swbctl", swbctlExecutable, "--timeout-ms", String(timeoutMs)];
        if (refresh)
            command.push("--refresh");

        refreshProcess.command = command;
        bridgeDeadline.interval = timeoutMs + 2000;
        bridgeDeadline.restart();
        refreshProcess.running = true;
        itemsChanged();
    }

    function setFailure(code, message, retryable) {
        currentFailure = {
            "code": code,
            "message": message,
            "retryable": retryable === true
        };
    }

    function scheduleStoppedRunCheck(generation) {
        Qt.callLater(() => {
            Qt.callLater(() => {
                return root.finishStoppedRunIfNeeded(generation, false);
            });
        });
    }

    function finishStoppedRunIfNeeded(generation, deadline) {
        const disposition = SwitchboardModelV4.stoppedRunDisposition({
            "runActive": runActive,
            "running": refreshProcess.running,
            "observedRunGeneration": generation,
            "runGeneration": runGeneration,
            "settingsGeneration": settingsGeneration,
            "runSettingsGeneration": runSettingsGeneration,
            "exitFinished": exitFinished,
            "runExpired": runExpired
        }, deadline);
        if (disposition === "none")
            return;

        if (disposition === "wait") {
            maybeFinishRun();
            return;
        }
        if (disposition === "stale") {
            queuedRun = true;
            queuedRefresh = queuedRefresh || runWasRefresh;
        } else if (disposition === "start_failed")
            setFailure("bridge_start_failed", "The bridge process could not be started.", true);
        else if (disposition === "incomplete")
            setFailure("qml_process_incomplete", "The bridge process stopped without complete exit notifications.", true);
        runExpired = true;
        stdoutFinished = true;
        stderrFinished = true;
        exitFinished = true;
        maybeFinishRun();
    }

    function maybeFinishRun() {
        if (!runActive || !stdoutFinished || !stderrFinished || !exitFinished)
            return;

        bridgeDeadline.stop();
        if (runSettingsGeneration !== settingsGeneration) {
            queuedRun = true;
            queuedRefresh = queuedRefresh || runWasRefresh;
        } else if (!runExpired) {
            const parsed = parseCurrentBridgeResponse(runStdout);
            if (runSettingsGeneration === settingsGeneration && !runExpired && runExitCode === 0 && parsed.ok) {
                lastGoodModel = parsed.model;
                const cacheChanged = persistentModelFingerprint(parsed.model) !== cachedModelFingerprint;
                if (!cacheLoadedFromState || runWasRefresh || cacheChanged)
                    saveCachedModel(parsed.model, runWasRefresh || cacheChanged);
                currentFailure = null;
                automaticRetryBudget = 1;
                if (!runWasRefresh && fleetIsStale(Date.now())) {
                    queuedRun = true;
                    queuedRefresh = true;
                }
            } else if (!parsed.ok) {
                setFailure(parsed.error.code, parsed.error.message, parsed.error.retryable);
                console.warn("Switchboard bridge read failed:", parsed.error.code, "exit=" + runExitCode, "outputChars=" + runStdout.length);
                if (lastGoodModel === null && automaticRetryBudget > 0) {
                    automaticRetryBudget -= 1;
                    automaticRetry.restart();
                }
            } else {
                setFailure("bridge_exit_mismatch", "The bridge exited unsuccessfully after returning a model.", true);
            }
        }
        runActive = false;
        itemsChanged();
        if (queuedRun) {
            const refresh = queuedRefresh;
            queuedRun = false;
            queuedRefresh = false;
            pendingRefresh = pendingRefresh || refresh;
            scheduleRun(pendingRefresh);
        }
    }

    Component.onCompleted: {
        loadSettings();
        loadCachedModel();
        scheduleRun(false);
    }
    onPluginServiceChanged: {
        if (pluginService) {
            loadSettings();
            loadCachedModel();
        }
    }

    Connections {
        function onPluginDataChanged(changedPluginId) {
            if (changedPluginId !== root.pluginName)
                return;

            root.loadSettings();
            root.scheduleRun(false);
        }

        target: root.pluginService
        enabled: root.pluginService !== null
    }

    Timer {
        id: automaticRetry

        interval: 250
        repeat: false
        onTriggered: root.scheduleRun(false)
    }

    Timer {
        id: cacheWriteFollowup

        interval: 350
        repeat: false
        onTriggered: {
            if (!root.lastGoodModel || !root.pluginService || typeof root.pluginService.savePluginState !== "function")
                return;
            try {
                root.pluginService.savePluginState(root.pluginName, root.modelStateKey, root.lastGoodModel);
                root.cacheLoadedFromState = true;
                root.cachedModelFingerprint = root.persistentModelFingerprint(root.lastGoodModel);
            } catch (error) {
                console.warn("Switchboard cached model follow-up write failed");
            }
        }
    }

    Timer {
        id: bridgeDeadline

        repeat: false
        onTriggered: {
            if (!root.runActive)
                return;

            if (!refreshProcess.running) {
                root.finishStoppedRunIfNeeded(root.runGeneration, true);
                return;
            }
            root.runExpired = true;
            if (root.runSettingsGeneration !== root.settingsGeneration) {
                root.queuedRun = true;
                root.queuedRefresh = root.queuedRefresh || root.runWasRefresh;
            } else {
                root.setFailure("qml_process_timeout", "The bridge did not finish before the launcher deadline.", true);
            }
            refreshProcess.signal(15);
            root.itemsChanged();
        }
    }

    Timer {
        id: actionDeadline

        repeat: false
        onTriggered: {
            if (!root.actionActive)
                return;

            root.actionExpired = true;
            root.setFailure("action_timeout", "The session opener did not finish before the launcher deadline.", true);
            actionProcess.signal(15);
            root.itemsChanged();
        }
    }

    Process {
        id: refreshProcess

        running: false
        onRunningChanged: {
            if (!running && root.runActive)
                root.scheduleStoppedRunCheck(root.runGeneration);
        }
        onExited: (exitCode, exitStatus) => {
            root.runExitCode = exitCode;
            root.exitFinished = true;
            root.maybeFinishRun();
        }

        stdout: StdioCollector {
            onStreamFinished: {
                root.runStdout = text;
                root.stdoutFinished = true;
                root.maybeFinishRun();
            }
        }

        stderr: StdioCollector {
            onStreamFinished: {
                root.stderrFinished = true;
                root.maybeFinishRun();
            }
        }
    }

    Process {
        id: managerProcess

        running: false
        onRunningChanged: {
            if (!running && root.managerActive)
                root.scheduleStoppedManagerCheck();
        }
        onExited: (exitCode, exitStatus) => {
            root.managerExitCode = exitCode;
            root.managerExitFinished = true;
            root.maybeFinishManager();
        }

        stdout: StdioCollector {
            onStreamFinished: {
                root.managerStdout = text;
                root.managerStdoutFinished = true;
                root.maybeFinishManager();
            }
        }

        stderr: StdioCollector {
            onStreamFinished: {
                root.managerStderrFinished = true;
                root.maybeFinishManager();
            }
        }
    }

    Process {
        id: actionProcess

        running: false
        onRunningChanged: {
            if (!running && root.actionActive)
                root.scheduleStoppedActionCheck();
        }
        onExited: (exitCode, exitStatus) => {
            root.actionExitCode = exitCode;
            root.actionExitFinished = true;
            root.maybeFinishAction();
        }

        stdout: StdioCollector {
            onStreamFinished: {
                root.actionStdout = text;
                root.actionStdoutFinished = true;
                root.maybeFinishAction();
            }
        }

        stderr: StdioCollector {
            onStreamFinished: {
                root.actionStderrFinished = true;
                root.maybeFinishAction();
            }
        }
    }

    IpcHandler {
        function status(): string {
            const model = root.lastGoodModel;
            const tasks = model && Array.isArray(model.tasks) ? model.tasks.length : 0;
            const inbox = model && Array.isArray(model.inboxSessions) ? model.inboxSessions.length : 0;
            return JSON.stringify({
                "bridgeVersion": root.bridgeContractVersion,
                "modelVersion": root.modelContractVersion,
                "idle": !root.runActive && !root.startScheduled,
                "runGeneration": root.runGeneration,
                "hasModel": model !== null,
                "taskCount": tasks,
                "inboxCount": inbox,
                "failureCode": root.currentFailure ? root.currentFailure.code : ""
            });
        }

        function refresh(): string {
            root.scheduleRun(true);
            return status();
        }

        target: "switchboard-launcher"
    }
}
