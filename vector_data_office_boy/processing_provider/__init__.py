"""QGIS Processing provider package for Vector Data Office Boy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .provider import VectorDataOfficeBoyProvider

__all__ = ["VectorDataOfficeBoyProvider"]


def __getattr__(name: str) -> Any:
    """Lazily expose VectorDataOfficeBoyProvider without failing outside QGIS."""
    if name == "VectorDataOfficeBoyProvider":
        from .provider import VectorDataOfficeBoyProvider

        return VectorDataOfficeBoyProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
