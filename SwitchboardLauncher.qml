import QtQuick

QtObject {
    property var pluginService: null
    property string trigger: "sb:"

    signal itemsChanged()

    function getItems(query) {
        return []
    }

    function executeItem(item) {
        return
    }
}
