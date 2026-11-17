"""Data models, error constants, and configuration schemas for topology auditing."""

from __future__ import annotations

from dataclasses import dataclass, field

ERROR_SELF_INTERSECTION = "RING_SELF_INTERSECTION"
ERROR_OVERLAP = "OVERLAP"
ERROR_GAP = "GAP"
ERROR_SLIVER = "SLIVER_POLYGON"

ERROR_COLORS: dict[str, dict[str, object]] = {
    ERROR_SELF_INTERSECTION: {
        "fill": "#FF8C00",
        "stroke": "#FF8C00",
        "stroke_width_mm": 5.0,
        "opacity": 0.65,
        "label": "Ring Self-Intersection",
    },
    ERROR_OVERLAP: {
        "fill": "#FF2400",
        "stroke": "#FF2400",
        "stroke_width_mm": 1.5,
        "opacity": 0.70,
        "label": "Overlap Polygon",
    },
    ERROR_GAP: {
        "fill": "#9B30FF",
        "stroke": "#9B30FF",
        "stroke_width_mm": 5.0,
        "opacity": 0.75,
        "label": "Internal Gap",
    },
    ERROR_SLIVER: {
        "fill": "#FFD700",
        "stroke": "#FFD700",
        "stroke_width_mm": 5.0,
        "opacity": 0.75,
        "label": "Sliver Polygon",
    },
}

ERROR_LAYER_NAMES: dict[str, str] = {
    ERROR_SELF_INTERSECTION: "Error_Ring_Self_Intersection",
    ERROR_OVERLAP: "Error_Overlap_Polygon",
    ERROR_GAP: "Error_Internal_Gap",
    ERROR_SLIVER: "Error_Sliver_Polygon",
}

SCAN_ATTRIBUTE_FIELDS: tuple[str, ...] = (
    "error_id",
    "error_type",
    "severity",
    "fid_1",
    "fid_2",
    "oid_ref",
    "area_m2",
    "perim_m",
    "pp_index",
    "error_x",
    "error_y",
)

VALID_OVERLAP_RULES: tuple[str, ...] = (
    "cut_hole_in_host",
    "subtract_from_larger",
    "keep_lower_fid",
)

VALID_GAP_MERGE_RULES: tuple[str, ...] = (
    "longest_shared_boundary",
    "largest_neighbor_area",
    "smallest_neighbor_area",
)

VALID_SLIVER_MERGE_RULES: tuple[str, ...] = (
    "longest_shared_boundary",
    "largest_neighbor_area",
    "same_attribute_class",
)


@dataclass
class SelfIntersectionConfig:
    """Configuration for ring self-intersection and boundary spike detection/repair."""

    detect_spikes: bool = True
    max_spike_angle_deg: float = 1.0
    max_spike_arm_m: float = 5.0
    min_part_area_m2: float = 0.01
    recalc_attributes: bool = True

    def __post_init__(self) -> None:
        if self.max_spike_angle_deg <= 0.0 or self.max_spike_angle_deg >= 180.0:
            raise ValueError("max_spike_angle_deg must be between 0 and 180 degrees.")
        if self.max_spike_arm_m <= 0.0:
            raise ValueError("max_spike_arm_m must be strictly positive.")
        if self.min_part_area_m2 < 0.0:
            raise ValueError("min_part_area_m2 cannot be negative.")


@dataclass
class OverlapConfig:
    """Configuration for polygon overlap detection and non-destructive resolution."""

    overlap_rule: str = "cut_hole_in_host"
    min_overlap_area_m2: float = 0.0001
    include_micro_overlaps: bool = False
    recalc_attributes: bool = True

    def __post_init__(self) -> None:
        if self.overlap_rule not in VALID_OVERLAP_RULES:
            raise ValueError(
                f"Unsupported overlap_rule '{self.overlap_rule}'. "
                f"Expected one of {VALID_OVERLAP_RULES}."
            )
        if self.min_overlap_area_m2 < 0.0:
            raise ValueError("min_overlap_area_m2 cannot be negative.")


@dataclass
class GapConfig:
    """Configuration for internal gap detection and full closure."""

    merge_rule: str = "longest_shared_boundary"
    recalc_attributes: bool = True

    def __post_init__(self) -> None:
        if self.merge_rule not in VALID_GAP_MERGE_RULES:
            raise ValueError(
                f"Unsupported gap merge_rule '{self.merge_rule}'. "
                f"Expected one of {VALID_GAP_MERGE_RULES}."
            )


@dataclass
class SliverConfig:
    """Configuration for sliver and multipart fragment detection and merging."""

    max_micro_sliver_area_m2: float = 10.0
    min_polsby_popper: float = 0.055
    max_thin_sliver_area_m2: float = 150.0
    min_multipart_area_m2: float = 1.0
    merge_rule: str = "longest_shared_boundary"
    recalc_attributes: bool = True

    def __post_init__(self) -> None:
        if self.merge_rule not in VALID_SLIVER_MERGE_RULES:
            raise ValueError(
                f"Unsupported sliver merge_rule '{self.merge_rule}'. "
                f"Expected one of {VALID_SLIVER_MERGE_RULES}."
            )
        if self.max_micro_sliver_area_m2 < 0.0 or self.max_thin_sliver_area_m2 < 0.0:
            raise ValueError("Sliver area thresholds cannot be negative.")
        if self.min_multipart_area_m2 < 0.0:
            raise ValueError("min_multipart_area_m2 cannot be negative.")
        if self.min_polsby_popper < 0.0 or self.min_polsby_popper > 1.0:
            raise ValueError("min_polsby_popper must be between 0.0 and 1.0.")


@dataclass
class TopologyErrorRecord:
    """Single spatial topology issue matching the 11-column diagnostic schema."""

    error_id: int
    error_type: str
    severity: str
    fid_1: int
    fid_2: str
    oid_ref: str
    area_m2: float
    perim_m: float
    pp_index: float
    error_x: float
    error_y: float
    geometry_wkb: bytes


@dataclass
class ScanResult:
    """Aggregated scan diagnostics for one topology error category."""

    error_type: str
    layer_name: str
    crs_wkt: str
    error_count: int
    total_area_m2: float
    records: list[TopologyErrorRecord] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)


@dataclass
class FixResult:
    """Summary of an automated topology repair and atomic source overwrite."""

    error_type: str
    fixed_count: int
    features_before: int
    features_after: int
    remaining_errors: int
    area_changed_m2: float
    output_path: str
    logs: list[str] = field(default_factory=list)
