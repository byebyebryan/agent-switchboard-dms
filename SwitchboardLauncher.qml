import QtQuick
import Quickshell
import Quickshell.Io
import "SwitchboardModel.js" as SwitchboardModel
import qs.Common

Item {
    id: root

    readonly property string pluginName: "switchboard"
    readonly property string bridgeExecutable: Paths.strip(Qt.resolvedUrl("switchboard-bridge"))
    readonly property string openerExecutable: Paths.strip(Qt.resolvedUrl("switchboard-open"))
    property var pluginService: null
    property string trigger: "sb:"
    property string swbctlExecutable: "swbctl"
    property string terminalExecutable: "ghostty"
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
        if (isNaN(parsed))
            return fallback;

        return Math.max(minimum, Math.min(maximum, parsed));
    }

    function loadSettings() {
        if (!pluginService)
            return;

        const configuredExecutable = pluginService.loadPluginData(pluginName, "swbctl", "swbctl");
        const nextExecutable = SwitchboardModel.boundedExecutable(configuredExecutable);
        const configuredTerminal = pluginService.loadPluginData(pluginName, "terminal", "ghostty");
        const nextTerminal = SwitchboardModel.boundedExecutable(configuredTerminal, "ghostty");
        const nextTimeout = boundedInteger(pluginService.loadPluginData(pluginName, "timeout_ms", 10000), 100, 60000, 10000);
        const nextRefresh = boundedInteger(pluginService.loadPluginData(pluginName, "refresh_seconds", 15), 5, 300, 15);
        const changed = nextExecutable !== swbctlExecutable || nextTerminal !== terminalExecutable || nextTimeout !== timeoutMs || nextRefresh !== refreshSeconds;
        swbctlExecutable = nextExecutable;
        terminalExecutable = nextTerminal;
        timeoutMs = nextTimeout;
        refreshSeconds = nextRefresh;
        if (changed)
            settingsGeneration += 1;
    }

    function snapshotIsStale(now) {
        return lastGoodModel !== null && SwitchboardModel.isStale(lastGoodModel, now, refreshSeconds);
    }

    function scheduleForRead() {
        const now = Date.now();
        if (lastGoodModel === null) {
            scheduleRun(false);
            return;
        }
        if (snapshotIsStale(now))
            scheduleRun(true);
    }

    function getItems(query) {
        Qt.callLater(root.scheduleForRead);
        const now = Date.now();
        return SwitchboardModel.launcherItems(lastGoodModel, query, {
            "now": now,
            "loading": runActive || startScheduled,
            "stale": snapshotIsStale(now),
            "failure": currentFailure
        });
    }

    function executeItem(item) {
        if (actionActive || !item || !item._windowHost)
            return;

        let targetArguments;
        if (item._switchboardKind === "session" && item._sessionKey) {
            targetArguments = [item._sessionKey];
        } else if (item._switchboardKind === "new" && item._projectId && item._locationId && item._provider) {
            targetArguments = ["--project", item._projectId, "--location", item._locationId, "--provider", item._provider];
        } else {
            return;
        }

        actionActive = true;
        actionExpired = false;
        actionStdoutFinished = false;
        actionStderrFinished = false;
        actionExitFinished = false;
        actionExitCode = -1;
        actionStdout = "";
        actionProcess.command = [openerExecutable, "--swbctl", swbctlExecutable, "--terminal", terminalExecutable, "--timeout-ms", String(timeoutMs), "--window-host", item._windowHost].concat(targetArguments);
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
            const parsed = SwitchboardModel.parseActionResponse(actionStdout);
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
        const plan = SwitchboardModel.planRunRequest({
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
        const disposition = SwitchboardModel.stoppedRunDisposition({
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
            const parsed = SwitchboardModel.parseBridgeResponse(runStdout);
            if (SwitchboardModel.shouldAcceptRunResult(runSettingsGeneration, settingsGeneration, runExpired, runExitCode, parsed.ok)) {
                lastGoodModel = parsed.model;
                currentFailure = null;
                if (!runWasRefresh && snapshotIsStale(Date.now())) {
                    queuedRun = true;
                    queuedRefresh = true;
                }
            } else if (!parsed.ok) {
                setFailure(parsed.error.code, parsed.error.message, parsed.error.retryable);
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
        scheduleRun(false);
    }
    onPluginServiceChanged: {
        if (pluginService)
            loadSettings();
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
}
