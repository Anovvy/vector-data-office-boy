"""Main QGIS plugin controller for Vector Data Office Boy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from vector_data_office_boy.processing_provider import VectorDataOfficeBoyProvider
from vector_data_office_boy.ui import OfficeBoyDialog


class VectorDataOfficeBoyPlugin:
    """Integrates Vector Data Office Boy into QGIS Plugins menu, toolbar, and Processing."""

    MENU_NAME = "&Vector Data Office Boy"

    def __init__(self, iface: Any) -> None:
        """Store QGIS interface handle and initialize plugin state."""
        self.iface = iface
        self.plugin_dir = Path(__file__).resolve().parent
        self.action: QAction | None = None
        self.dialog: OfficeBoyDialog | None = None
        self.provider: VectorDataOfficeBoyProvider | None = None

    def _build_icon(self) -> QIcon:
        """Load the plugin icon from resources/icon.png if present."""
        icon_path = self.plugin_dir / "resources" / "icon.png"
        if icon_path.is_file():
            return QIcon(str(icon_path))
        return QIcon()

    def initProcessing(self) -> None:
        """Register the Vector Data Office Boy Processing provider."""
        registry = QgsApplication.processingRegistry()
        if registry is None:
            return
        if self.provider is None:
            self.provider = VectorDataOfficeBoyProvider()
        if registry.providerById(self.provider.id()) is None:
            registry.addProvider(self.provider)

    def initGui(self) -> None:
        """Register toolbar button, Plugins menu item, and Processing provider."""
        parent = self.iface.mainWindow() if self.iface is not None else None
        self.action = QAction(
            self._build_icon(),
            "Vector Data Office Boy",
            parent,
        )
        self.action.setObjectName("actionVectorDataOfficeBoy")
        self.action.setStatusTip(
            "Open Vector Data Office Boy topology auditing and geometry cleaning tool"
        )
        self.action.setToolTip("Vector Data Office Boy")
        self.action.triggered.connect(self.run)

        if self.iface is not None:
            self.iface.addPluginToMenu(self.MENU_NAME, self.action)
            self.iface.addToolBarIcon(self.action)

        self.initProcessing()

    def unload(self) -> None:
        """Cleanly remove GUI items, close non-modal dialog, and unregister Processing."""
        if self.dialog is not None:
            self.dialog.cleanup()
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

        if self.action is not None:
            if self.iface is not None:
                self.iface.removePluginMenu(self.MENU_NAME, self.action)
                self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None

        if self.provider is not None:
            registry = QgsApplication.processingRegistry()
            if registry is not None:
                registry.removeProvider(self.provider)
            self.provider = None

    def run(self) -> None:
        """Open the non-modal Vector Data Office Boy dialog."""
        if self.dialog is None:
            parent = self.iface.mainWindow() if self.iface is not None else None
            self.dialog = OfficeBoyDialog(iface=self.iface, parent=parent)
        else:
            self.dialog.refresh_layers()

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()


__all__ = ["VectorDataOfficeBoyPlugin"]
