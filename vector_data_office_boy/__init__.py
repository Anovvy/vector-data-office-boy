"""QGIS plugin entrypoint for Vector Data Office Boy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plugin import VectorDataOfficeBoyPlugin

__version__ = "1.0.3"
__all__ = ["classFactory", "__version__"]


def classFactory(iface: Any) -> VectorDataOfficeBoyPlugin:
    """Instantiate the QGIS plugin controller.

    :param iface: QGIS interface instance (QgisInterface).
    :returns: Initialized VectorDataOfficeBoyPlugin instance.
    """
    from .plugin import VectorDataOfficeBoyPlugin

    return VectorDataOfficeBoyPlugin(iface)
