"""Qt UI package for Vector Data Office Boy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .office_boy_dialog import OfficeBoyDialog

__all__ = ["OfficeBoyDialog"]


def __getattr__(name: str) -> Any:
    """Lazily expose OfficeBoyDialog without failing outside QGIS."""
    if name == "OfficeBoyDialog":
        from .office_boy_dialog import OfficeBoyDialog

        return OfficeBoyDialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
