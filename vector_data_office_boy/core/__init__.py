"""Core spatial topology auditing and repair engine for Vector Data Office Boy."""

from __future__ import annotations

from vector_data_office_boy.core.detector import (
    scan_internal_gaps,
    scan_overlaps,
    scan_ring_self_intersections,
    scan_slivers,
)
from vector_data_office_boy.core.fixer_gap import fix_internal_gaps
from vector_data_office_boy.core.fixer_overlap import fix_overlaps
from vector_data_office_boy.core.fixer_self_intersection import fix_ring_self_intersections
from vector_data_office_boy.core.fixer_sliver import fix_slivers
from vector_data_office_boy.core.geom_backend import (
    MetricCrsContext,
    build_metric_context,
    extract_polygonal,
    polsby_popper,
    restore_z,
    safe_make_valid,
    to_2d,
)
from vector_data_office_boy.core.models import (
    ERROR_COLORS,
    ERROR_GAP,
    ERROR_LAYER_NAMES,
    ERROR_OVERLAP,
    ERROR_SELF_INTERSECTION,
    ERROR_SLIVER,
    SCAN_ATTRIBUTE_FIELDS,
    FixResult,
    GapConfig,
    OverlapConfig,
    ScanResult,
    SelfIntersectionConfig,
    SliverConfig,
    TopologyErrorRecord,
)
from vector_data_office_boy.core.overwrite_writer import (
    LoadedLayerData,
    atomic_overwrite_vector_source,
    load_vector_source,
)
from vector_data_office_boy.core.symbology import (
    add_or_replace_error_layer_in_project,
    apply_single_error_symbology,
    create_temporary_error_layer,
)

__all__ = [
    "ERROR_COLORS",
    "ERROR_GAP",
    "ERROR_LAYER_NAMES",
    "ERROR_OVERLAP",
    "ERROR_SELF_INTERSECTION",
    "ERROR_SLIVER",
    "FixResult",
    "GapConfig",
    "LoadedLayerData",
    "MetricCrsContext",
    "OverlapConfig",
    "SCAN_ATTRIBUTE_FIELDS",
    "ScanResult",
    "SelfIntersectionConfig",
    "SliverConfig",
    "TopologyErrorRecord",
    "add_or_replace_error_layer_in_project",
    "apply_single_error_symbology",
    "atomic_overwrite_vector_source",
    "build_metric_context",
    "create_temporary_error_layer",
    "extract_polygonal",
    "fix_internal_gaps",
    "fix_overlaps",
    "fix_ring_self_intersections",
    "fix_slivers",
    "load_vector_source",
    "polsby_popper",
    "restore_z",
    "safe_make_valid",
    "scan_internal_gaps",
    "scan_overlaps",
    "scan_ring_self_intersections",
    "scan_slivers",
    "to_2d",
]
