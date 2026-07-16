import QtQuick
import "SwitchboardModel.js" as SwitchboardModel
import qs.Common
import qs.Modules.Plugins
import qs.Widgets

PluginSettings {
    id: root

    pluginId: "switchboard"

    StyledText {
        width: parent.width
        text: "Switchboard"
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Font.Bold
        color: Theme.surfaceText
    }

    StyledText {
        width: parent.width
        text: "Read-only local Codex sessions from the public Agent Switchboard snapshot API."
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.surfaceVariantText
        wrapMode: Text.WordWrap
    }

    Column {
        id: executableSetting

        property string value: "swbctl"
        property bool isInitialized: false

        function findSettings() {
            let item = parent;
            while (item) {
                if (item.saveValue !== undefined && item.loadValue !== undefined)
                    return item;

                item = item.parent;
            }
            return null;
        }

        function loadValue() {
            const settings = findSettings();
            if (!settings || !settings.pluginService)
                return;

            const loadedValue = String(settings.loadValue("swbctl", "swbctl") || "swbctl");
            const boundedValue = SwitchboardModel.boundedExecutable(loadedValue);
            if (executableField.activeFocus && isInitialized)
                return;

            value = boundedValue;
            executableField.text = boundedValue;
            isInitialized = true;
            if (boundedValue !== loadedValue)
                settings.saveValue("swbctl", boundedValue);
        }

        function commit() {
            if (!isInitialized)
                return;

            const boundedValue = SwitchboardModel.boundedExecutable(executableField.text);
            if (executableField.text !== boundedValue)
                executableField.text = boundedValue;

            if (boundedValue === value)
                return;

            value = boundedValue;
            const settings = findSettings();
            if (settings)
                settings.saveValue("swbctl", boundedValue);
        }

        width: parent.width
        spacing: Theme.spacingS
        Component.onCompleted: Qt.callLater(loadValue)

        StyledText {
            text: "swbctl executable"
            font.pixelSize: Theme.fontSizeMedium
            font.weight: Font.Medium
            color: Theme.surfaceText
        }

        StyledText {
            width: parent.width
            text: "One executable path or command name, limited to 4096 UTF-16 code units. The value is passed as one argv token and is never shell-split."
            font.pixelSize: Theme.fontSizeSmall
            color: Theme.surfaceVariantText
            wrapMode: Text.WordWrap
        }

        DankTextField {
            id: executableField

            width: parent.width
            placeholderText: "swbctl"
            maximumLength: SwitchboardModel.MAX_EXECUTABLE_LENGTH
            onEditingFinished: executableSetting.commit()
            onActiveFocusChanged: {
                if (!activeFocus)
                    executableSetting.commit();
            }
        }
    }

    SliderSetting {
        settingKey: "timeout_ms"
        label: "Snapshot timeout"
        description: "Maximum time allowed for swbctl to produce one validated snapshot."
        defaultValue: 10000
        minimum: 100
        maximum: 60000
        unit: " ms"
    }

    SliderSetting {
        settingKey: "refresh_seconds"
        label: "Refresh interval"
        description: "Age at which a cached snapshot requests a full background refresh."
        defaultValue: 15
        minimum: 5
        maximum: 300
        unit: " s"
    }

    StyledText {
        width: parent.width
        text: "Opening a session is intentionally unavailable until Switchboard publishes a versioned action contract."
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.surfaceVariantText
        wrapMode: Text.WordWrap
    }
}
