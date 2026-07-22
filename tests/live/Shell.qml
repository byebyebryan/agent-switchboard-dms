import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root

    property bool queryMatchedExact: false
    property bool categoriesValid: false
    property bool cacheReloaded: false
    property int retainedCount: 0
    property string retainedFingerprint: ""

    function fingerprint() {
        return launcher.lastGoodModel ? JSON.stringify(launcher.lastGoodModel) : "";
    }

    function summary() {
        const model = launcher.lastGoodModel;
        return JSON.stringify({
            "idle": !launcher.runActive && !launcher.actionActive,
            "runGeneration": launcher.runGeneration,
            "hasModel": model !== null,
            "fresh": launcher.modelFresh,
            "viewCount": model ? model.views.length : 0,
            "projectCount": model ? model.projects.length : 0,
            "recoveryCount": model ? model.recoveries.length : 0,
            "entryCount": model ? model.views.length + model.projects.length + model.recoveries.length : 0,
            "failureCode": launcher.currentFailure ? launcher.currentFailure.code : "",
            "queryMatchedExact": root.queryMatchedExact,
            "categoriesValid": root.categoriesValid,
            "cacheReloaded": root.cacheReloaded,
            "retentionBaselineCount": root.retainedCount,
            "retainedModelMatches": root.retainedFingerprint !== "" && root.fingerprint() === root.retainedFingerprint,
            "validatorRejectedInvalid": !launcher.parseBridge("{\"bridgeVersion\":4}").ok,
            "settingsHeightPositive": settingsRoot.implicitHeight > 0,
            "swbctlConfigured": launcher.swbctlExecutable.length > 0
        });
    }

    QtObject {
        id: testPluginService

        property string swbctl: Quickshell.env("SWITCHBOARD_LIVE_SWBCTL") || "swbctl"
        property int timeoutMs: 10000
        property int refreshSeconds: 300
        property var pluginState: ({})
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

        function loadPluginState(pluginId, key, defaultValue) {
            if (pluginId !== "switchboard")
                return defaultValue;
            return pluginState[key] === undefined ? defaultValue : pluginState[key];
        }

        function savePluginState(pluginId, key, value) {
            if (pluginId === "switchboard")
                pluginState[key] = value;
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
        target: "switchboard-live"

        function status(): string {
            return root.summary();
        }

        function captureBaseline(): string {
            const model = launcher.lastGoodModel;
            root.queryMatchedExact = false;
            if (!model)
                return root.summary();
            if (model.views.length > 0) {
                launcher.setCategory("");
                const view = model.views[0];
                const items = launcher.getItems(view.title);
                root.queryMatchedExact = items.filter(item => item._switchboardKind === "view" && item._targetId === view.viewId).length === 1;
            } else if (model.projects.length > 0) {
                launcher.setCategory("projects");
                const project = model.projects[0];
                const items = launcher.getItems(project.name);
                root.queryMatchedExact = items.filter(item => item._switchboardKind === "project" && item._targetId === project.projectId).length === 1;
            }
            return root.summary();
        }

        function captureCategories(): string {
            const model = launcher.lastGoodModel;
            const categories = launcher.getCategories();
            root.categoriesValid = categories.length >= 2 && categories[0].name === "Views" && categories[1].name === "Projects" && (!model || model.recoveries.length === 0 || categories.some(category => category.name === "Recovery"));
            return root.summary();
        }

        function reloadCachedModel(): string {
            const before = root.fingerprint();
            launcher.lastGoodModel = null;
            launcher.modelFresh = false;
            launcher.loadCache();
            root.cacheReloaded = before !== "" && root.fingerprint() === before && !launcher.modelFresh;
            return root.summary();
        }

        function captureRetentionBaseline(): string {
            const model = launcher.lastGoodModel;
            root.retainedCount = model ? model.views.length + model.projects.length + model.recoveries.length : 0;
            root.retainedFingerprint = root.fingerprint();
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
    }
}
