import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root

    property string capturedSessionId: ""
    property string capturedSessionKey: ""
    property int retentionSessionCount: 0
    property string retentionFingerprint: ""
    property double refreshBaselineGeneratedAt: -1
    property bool queryMatchedExact: false

    function modelFingerprint() {
        return launcher.lastGoodModel ? JSON.stringify(launcher.lastGoodModel) : "";
    }

    function summary() {
        const model = launcher.lastGoodModel;
        const sessions = model && Array.isArray(model.sessions) ? model.sessions : [];
        return JSON.stringify({
            "idle": !launcher.runActive && !launcher.startScheduled,
            "runGeneration": launcher.runGeneration,
            "runWasRefresh": launcher.runWasRefresh,
            "hasModel": model !== null,
            "sessionCount": sessions.length,
            "failureCode": launcher.currentFailure ? launcher.currentFailure.code : "",
            "queryMatchedExact": root.queryMatchedExact,
            "refreshGeneratedAtAdvanced": model !== null && root.refreshBaselineGeneratedAt >= 0 && model.generatedAt > root.refreshBaselineGeneratedAt,
            "retentionBaselineCount": root.retentionSessionCount,
            "retainedModelMatches": root.retentionFingerprint !== "" && root.modelFingerprint() === root.retentionFingerprint,
            "settingsHeightPositive": settingsRoot.implicitHeight > 0,
            "settingsFocused": settingsRoot.focus || settingsRoot.activeFocus,
            "swbctlConfigured": launcher.swbctlExecutable.length > 0
        });
    }

    QtObject {
        id: testPluginService

        property string swbctl: Quickshell.env("SWITCHBOARD_LIVE_SWBCTL") || "swbctl"
        property int timeoutMs: 10000
        property int refreshSeconds: 300
        property var availablePlugins: ({
                "switchboard": {
                    "id": "switchboard",
                    "permissions": ["settings_read", "settings_write", "process"]
                }
            })

        signal pluginDataChanged(string pluginId)

        function loadPluginData(pluginId, key, defaultValue) {
            if (pluginId !== "switchboard")
                return defaultValue;

            if (key === "swbctl")
                return swbctl;

            if (key === "timeout_ms")
                return timeoutMs;

            if (key === "refresh_seconds")
                return refreshSeconds;

            return defaultValue;
        }

        function savePluginData(pluginId, key, value) {
            if (pluginId !== "switchboard")
                return false;

            if (key === "swbctl")
                swbctl = String(value);
            else if (key === "timeout_ms")
                timeoutMs = Number(value);
            else if (key === "refresh_seconds")
                refreshSeconds = Number(value);
            pluginDataChanged(pluginId);
            return true;
        }

        function getPluginVariants(pluginId) {
            return [];
        }
    }

    SwitchboardLauncher {
        id: launcher

        pluginService: testPluginService
    }

    FloatingWindow {
        id: settingsWindow

        visible: true
        implicitWidth: 520
        implicitHeight: Math.max(240, settingsRoot.implicitHeight)
        color: "transparent"

        SwitchboardSettings {
            id: settingsRoot

            anchors.fill: parent
            pluginService: testPluginService
        }
    }

    IpcHandler {
        function status(): string {
            return root.summary();
        }

        function captureBaseline(): string {
            const model = launcher.lastGoodModel;
            const sessions = model && Array.isArray(model.sessions) ? model.sessions : [];
            root.queryMatchedExact = false;
            if (sessions.length === 0)
                return root.summary();

            root.capturedSessionId = sessions[0].providerSessionId;
            root.capturedSessionKey = sessions[0].sessionKey;
            root.refreshBaselineGeneratedAt = model.generatedAt;
            const items = launcher.getItems(root.capturedSessionId);
            let matches = 0;
            for (let index = 0; index < items.length; index++) {
                if (items[index]._switchboardKind === "session" && items[index]._sessionKey === root.capturedSessionKey)
                    matches += 1;
            }
            root.queryMatchedExact = matches === 1;
            return root.summary();
        }

        function captureRetentionBaseline(): string {
            const model = launcher.lastGoodModel;
            root.retentionSessionCount = model && Array.isArray(model.sessions) ? model.sessions.length : 0;
            root.retentionFingerprint = root.modelFingerprint();
            return root.summary();
        }

        function refresh(): string {
            launcher.scheduleRun(true);
            return status();
        }

        function setExecutable(executable: string): string {
            testPluginService.swbctl = executable;
            testPluginService.pluginDataChanged("switchboard");
            return status();
        }

        function focusSettings(): string {
            settingsRoot.forceActiveFocus();
            return status();
        }

        target: "switchboard-live"
    }
}
