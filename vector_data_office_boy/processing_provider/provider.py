"""QGIS Processing Provider for Vector Data Office Boy."""

from __future__ import annotations

from pathlib import Path

from vector_data_office_boy.processing_provider.algorithms import ALL_ALGORITHMS

try:
    from qgis.core import QgsProcessingProvider
    from qgis.PyQt.QtGui import QIcon

    QGIS_AVAILABLE = True
except ImportError:  # pragma: no cover - allows importing package outside QGIS
    QGIS_AVAILABLE = False
    QgsProcessingProvider = object  # type: ignore[assignment,misc]
    QIcon = None  # type: ignore[assignment,misc]


class VectorDataOfficeBoyProvider(QgsProcessingProvider):
    """Registers the 4 Scan and 4 Fix topology algorithms in QGIS Processing."""

    def id(self) -> str:
        """Return unique provider ID."""
        return "vector_data_office_boy"

    def name(self) -> str:
        """Return provider display name shown in the Processing Toolbox."""
        return "Vector Data Office Boy"

    def longName(self) -> str:
        """Return descriptive long provider name."""
        return "Vector Data Office Boy - Polygon Topology Audit and Cleaning"

    def icon(self) -> QIcon:
        """Return plugin icon for the Processing Toolbox."""
        if not QGIS_AVAILABLE or QIcon is None:
            return None  # type: ignore[return-value]
        icon_path = Path(__file__).resolve().parent.parent / "resources" / "icon.png"
        if icon_path.is_file():
            return QIcon(str(icon_path))
        return QIcon()

    def loadAlgorithms(self) -> None:
        """Load all Scan and Fix algorithms into the provider."""
        if not QGIS_AVAILABLE:
            return
        for alg_cls in ALL_ALGORITHMS:
            self.addAlgorithm(alg_cls())


__all__ = ["QGIS_AVAILABLE", "VectorDataOfficeBoyProvider"]
