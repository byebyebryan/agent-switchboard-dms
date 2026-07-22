import QtQuick
import Quickshell
import Quickshell.Io
import "SwitchboardEntryModelV1.js" as EntryModel
import qs.Common
import qs.Services

Item {
    id: root

    readonly property string pluginName: "switchboard"
    readonly property string modelStateKey: "last_good_switchboard_entry_model_v1"
    readonly property int bridgeContractVersion: 1
    readonly property int modelContractVersion: 1
    readonly property string adapterVersion: "0.5.0"
    readonly property string bridgeExecutable: Paths.strip(Qt.resolvedUrl("switchboard-bridge"))
    readonly property string openerExecutable: Paths.strip(Qt.resolvedUrl("switchboard-open"))
    property var pluginService: null
    property string trigger: "sb:"
    property string swbctlExecutable: "swbctl"
    property string terminalExecutable: "ghostty"
    property string activeCategory: ""
    property int timeoutMs: 10000
    property int refreshSeconds: 15
    property var lastGoodModel: null
    property var currentFailure: null
    property bool modelFresh: false
    property bool runActive: false
    property bool runExpired: false
    property bool runRefresh: false
    property bool queuedRefresh: false
    property bool stdoutFinished: false
    property bool stderrFinished: false
    property bool exitFinished: false
    property int runExitCode: -1
    property string runStdout: ""
    property int runGeneration: 0
    property int settingsGeneration: 0
    property int runSettingsGeneration: -1
    property bool actionActive: false
    property bool actionExpired: false
    property bool actionStdoutFinished: false
    property bool actionStderrFinished: false
    property bool actionExitFinished: false
    property int actionExitCode: -1
    property string actionStdout: ""

    signal itemsChanged

    function boundedInteger(value, minimum, maximum, fallback) {
        const parsed = parseInt(value);
        return isNaN(parsed) ? fallback : Math.max(minimum, Math.min(maximum, parsed));
    }

    function parseBridge(text) {
        let envelope;
        try {
            envelope = JSON.parse(String(text));
        } catch (error) {
            return {
                "ok": false,
                "error": {
                    "code": "bridge_invalid_json",
                    "message": "The bridge returned invalid JSON.",
                    "retryable": false
                }
            };
        }
        if (!envelope || envelope.bridgeVersion !== bridgeContractVersion || typeof envelope.ok !== "boolean")
            return {
                "ok": false,
                "error": {
                    "code": "bridge_incompatible",
                    "message": "DMS 0.5 requires bridge v1 and core 0.3.",
                    "retryable": false
                }
            };
        if (!envelope.ok)
            return envelope.error && typeof envelope.error.code === "string" && typeof envelope.error.message === "string" ? {
                "ok": false,
                "error": envelope.error
            } : {
                "ok": false,
                "error": {
                    "code": "bridge_invalid_error",
                    "message": "The bridge returned an invalid error.",
                    "retryable": false
                }
            };
        if (!EntryModel.validateModel(envelope.model))
            return {
                "ok": false,
                "error": {
                    "code": "bridge_invalid_model",
                    "message": "The bridge returned an invalid entry model.",
                    "retryable": false
                }
            };
        return {
            "ok": true,
            "model": envelope.model
        };
    }

    function loadSettings() {
        if (!pluginService)
            return;
        const executable = EntryModel.boundedExecutable(pluginService.loadPluginData(pluginName, "swbctl", "swbctl"), "swbctl");
        const terminal = EntryModel.boundedExecutable(pluginService.loadPluginData(pluginName, "terminal", "ghostty"), "ghostty");
        const timeout = boundedInteger(pluginService.loadPluginData(pluginName, "timeout_ms", 10000), 100, 60000, 10000);
        const refresh = boundedInteger(pluginService.loadPluginData(pluginName, "refresh_seconds", 15), 5, 300, 15);
        if (executable !== swbctlExecutable || terminal !== terminalExecutable || timeout !== timeoutMs || refresh !== refreshSeconds)
            settingsGeneration += 1;
        swbctlExecutable = executable;
        terminalExecutable = terminal;
        timeoutMs = timeout;
        refreshSeconds = refresh;
    }

    function loadCache() {
        if (!pluginService || typeof pluginService.loadPluginState !== "function")
            return;
        try {
            const envelope = pluginService.loadPluginState(pluginName, modelStateKey, null);
            const cached = EntryModel.cachedModel(envelope);
            if (cached !== null) {
                lastGoodModel = cached;
                modelFresh = false;
            }
        } catch (error) {
            console.warn("Switchboard entry cache read failed");
        }
    }

    function saveCache(model) {
        if (!pluginService || typeof pluginService.savePluginState !== "function")
            return;
        const envelope = EntryModel.cacheEnvelope(model);
        if (envelope === null)
            return;
        try {
            pluginService.savePluginState(pluginName, modelStateKey, envelope);
        } catch (error) {
            console.warn("Switchboard entry cache write failed");
        }
    }

    function stale(now) {
        return EntryModel.isStale(lastGoodModel, now, refreshSeconds);
    }

    function getItems(query) {
        Qt.callLater(root.ensureRead);
        return EntryModel.launcherItems(lastGoodModel, query, {
            "now": Date.now(),
            "loading": runActive,
            "stale": stale(Date.now()),
            "fresh": modelFresh,
            "failure": currentFailure,
            "category": activeCategory
        });
    }

    function getCategories() {
        return EntryModel.launcherCategories(lastGoodModel);
    }

    function setCategory(categoryId) {
        activeCategory = String(categoryId || "");
    }

    function getContextMenuActions(item) {
        return [];
    }

    function executeItem(item) {
        if (!item || actionActive || !modelFresh || !item._hostId || !item._targetId)
            return;
        if (item._switchboardKind === "recovery" && item._actionability === "manual") {
            ToastService.showWarning("Manual Switchboard recovery required", item.comment);
            return;
        }
        if (["view", "project", "recovery"].indexOf(item._switchboardKind) === -1)
            return;
        startAction(item);
    }

    function startAction(item) {
        actionActive = true;
        actionExpired = false;
        actionStdoutFinished = false;
        actionStderrFinished = false;
        actionExitFinished = false;
        actionExitCode = -1;
        actionStdout = "";
        actionProcess.command = [openerExecutable, "--swbctl", swbctlExecutable, "--terminal", terminalExecutable, "--timeout-ms", String(timeoutMs), "--host", item._hostId, "--" + item._switchboardKind, item._targetId];
        actionDeadline.interval = timeoutMs * 2 + 3000;
        actionDeadline.restart();
        actionProcess.running = true;
        itemsChanged();
    }

    function maybeFinishAction() {
        if (!actionActive || !actionStdoutFinished || !actionStderrFinished || !actionExitFinished)
            return;
        actionDeadline.stop();
        const parsed = EntryModel.parseActionResponse(actionStdout);
        if (actionExpired) {
            // Preserve the timeout/start failure already published.
        } else if (actionExitCode === 0 && parsed.ok) {
            currentFailure = null;
            scheduleRun(true);
        } else if (!parsed.ok)
            currentFailure = parsed.error;
        else
            currentFailure = {
                "code": "action_exit_mismatch",
                "message": "The desktop helper exited without a valid result.",
                "retryable": true
            };
        actionActive = false;
        itemsChanged();
    }

    function ensureRead() {
        if (lastGoodModel === null)
            scheduleRun(false);
        else if (!modelFresh || stale(Date.now()))
            scheduleRun(true);
    }

    function scheduleRun(refresh) {
        if (runActive) {
            queuedRefresh = queuedRefresh || refresh;
            return;
        }
        runGeneration += 1;
        runActive = true;
        runExpired = false;
        runRefresh = refresh;
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

    function maybeFinishRun() {
        if (!runActive || !stdoutFinished || !stderrFinished || !exitFinished)
            return;
        bridgeDeadline.stop();
        const parsed = parseBridge(runStdout);
        if (runExpired) {
            modelFresh = false;
        } else if (runSettingsGeneration !== settingsGeneration) {
            modelFresh = false;
            queuedRefresh = true;
        } else if (runExitCode === 0 && parsed.ok) {
            lastGoodModel = parsed.model;
            modelFresh = true;
            currentFailure = null;
            saveCache(parsed.model);
        } else if (!parsed.ok) {
            modelFresh = false;
            currentFailure = parsed.error;
        } else {
            modelFresh = false;
            currentFailure = {
                "code": "bridge_exit_mismatch",
                "message": "The bridge exited without a valid model.",
                "retryable": true
            };
        }
        runActive = false;
        itemsChanged();
        if (queuedRefresh) {
            queuedRefresh = false;
            scheduleRun(true);
        }
    }

    function scheduleStoppedRunCheck(generation) {
        Qt.callLater(() => {
            Qt.callLater(() => {
                if (!root.runActive || refreshProcess.running || root.exitFinished || generation !== root.runGeneration)
                    return;
                root.runExpired = true;
                root.stdoutFinished = true;
                root.stderrFinished = true;
                root.exitFinished = true;
                root.currentFailure = {
                    "code": "bridge_start_failed",
                    "message": "The bridge process could not be started.",
                    "retryable": true
                };
                root.maybeFinishRun();
            });
        });
    }

    function scheduleStoppedActionCheck() {
        Qt.callLater(() => {
            Qt.callLater(() => {
                if (!root.actionActive || actionProcess.running || root.actionExitFinished)
                    return;
                root.actionExpired = true;
                root.actionStdoutFinished = true;
                root.actionStderrFinished = true;
                root.actionExitFinished = true;
                root.currentFailure = {
                    "code": "action_start_failed",
                    "message": "The desktop helper process could not be started.",
                    "retryable": true
                };
                root.maybeFinishAction();
            });
        });
    }

    Component.onCompleted: {
        loadSettings();
        loadCache();
        scheduleRun(false);
    }

    onPluginServiceChanged: {
        if (pluginService) {
            loadSettings();
            loadCache();
            ensureRead();
        }
    }

    Connections {
        target: root.pluginService
        enabled: root.pluginService !== null

        function onPluginDataChanged(changedPluginId) {
            if (changedPluginId !== root.pluginName)
                return;
            root.loadSettings();
            root.modelFresh = false;
            root.scheduleRun(true);
        }
    }

    Timer {
        id: bridgeDeadline
        repeat: false
        onTriggered: {
            if (!root.runActive)
                return;
            root.currentFailure = {
                "code": "qml_process_timeout",
                "message": "The bridge did not finish before the launcher deadline.",
                "retryable": true
            };
            root.runExpired = true;
            root.modelFresh = false;
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
            root.currentFailure = {
                "code": "action_timeout",
                "message": "The desktop helper did not finish before the launcher deadline.",
                "retryable": true
            };
            root.actionExpired = true;
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
        target: "switchboard-launcher"

        function status(): string {
            return JSON.stringify({
                "adapterVersion": root.adapterVersion,
                "bridgeVersion": root.bridgeContractVersion,
                "modelVersion": root.modelContractVersion,
                "idle": !root.runActive && !root.actionActive,
                "runGeneration": root.runGeneration,
                "hasModel": root.lastGoodModel !== null,
                "fresh": root.modelFresh,
                "viewCount": root.lastGoodModel ? root.lastGoodModel.views.length : 0,
                "projectCount": root.lastGoodModel ? root.lastGoodModel.projects.length : 0,
                "recoveryCount": root.lastGoodModel ? root.lastGoodModel.recoveries.length : 0,
                "failureCode": root.currentFailure ? root.currentFailure.code : ""
            });
        }

        function refresh(): string {
            root.scheduleRun(true);
            return status();
        }
    }
}
