import QtQuick
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.jonatanferm.agent-desktop"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "Agent GUI"
    tooltipText: "Toggle the separate agent desktop"
    onPressed: function(b) {
      if (b === Qt.LeftButton && root.bar)
        root.bar.run("~/.config/omarchy/plugins/io.github.jonatanferm.agent-desktop/bin/agent-desktop toggle")
    }
  }
}
