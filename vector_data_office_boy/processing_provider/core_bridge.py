"""Shared bridge between UI/Processing wrappers and vector_data_office_boy.core."""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from enum import Enum
from typing import Any, Literal, get_args, get_origin, get_type_hints

OVERLAP_RULE_LABELS: tuple[str, ...] = (
    "Cut Hole in Host Polygon (Keep All Features)",
    "Subtract Overlap from Larger Polygon",
    "Keep Lower FID (Subtract from Higher FID)",
)

GAP_RULE_LABELS: tuple[str, ...] = (
    "Longest Shared Boundary",
    "Largest Neighbor Area",
    "Smallest Neighbor Area",
)

SLIVER_RULE_LABELS: tuple[str, ...] = (
    "Longest Shared Boundary",
    "Largest Neighbor Area",
    "Same Attribute Class (TEMA/JENIS)",
)

_RULE_KEYWORDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "overlap": (
        ("cut_hole_in_host", "cut_hole", "option_a", "cut", "hole", "host", "keep_all"),
        ("subtract_from_larger", "subtract_larger", "larger", "from_larger", "option_b"),
        ("keep_lower_fid", "lower_fid", "keep_lower", "higher_fid", "option_c"),
    ),
    "gap": (
        ("longest_shared_boundary", "longest_shared", "longest_boundary", "shared_boundary"),
        ("largest_neighbor_area", "largest_neighbor", "largest_area", "largest"),
        ("smallest_neighbor_area", "smallest_neighbor", "smallest_area", "smallest"),
    ),
    "sliver": (
        ("longest_shared_boundary", "longest_shared", "longest_boundary", "shared_boundary"),
        ("largest_neighbor_area", "largest_neighbor", "largest_area", "largest"),
        ("same_attribute_class", "same_attribute", "same_class", "attribute", "tema", "jenis"),
    ),
}

_RULE_FALLBACK_SLUGS: dict[str, tuple[str, ...]] = {
    "overlap": ("cut_hole_in_host", "subtract_from_larger", "keep_lower_fid"),
    "gap": ("longest_shared_boundary", "largest_neighbor_area", "smallest_neighbor_area"),
    "sliver": ("longest_shared_boundary", "largest_neighbor_area", "same_attribute_class"),
}

_CORE_IMPORT_ERROR: ImportError | None = None

try:
    from vector_data_office_boy.core import models as core_models
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
    from vector_data_office_boy.core.models import (
        ERROR_GAP,
        ERROR_OVERLAP,
        ERROR_SELF_INTERSECTION,
        ERROR_SLIVER,
        FixResult,
        GapConfig,
        OverlapConfig,
        ScanResult,
        SelfIntersectionConfig,
        SliverConfig,
    )
    from vector_data_office_boy.core.symbology import add_or_replace_error_layer_in_project
except ImportError as exc:  # pragma: no cover - allows standalone inspection before core merge
    _CORE_IMPORT_ERROR = exc
    core_models = None  # type: ignore[assignment]
    ERROR_SELF_INTERSECTION = "ring_self_intersection"
    ERROR_OVERLAP = "overlap"
    ERROR_GAP = "internal_gap"
    ERROR_SLIVER = "sliver"
    SelfIntersectionConfig = None  # type: ignore[assignment,misc]
    OverlapConfig = None  # type: ignore[assignment,misc]
    GapConfig = None  # type: ignore[assignment,misc]
    SliverConfig = None  # type: ignore[assignment,misc]
    ScanResult = Any  # type: ignore[assignment,misc]
    FixResult = Any  # type: ignore[assignment,misc]
    scan_ring_self_intersections = None  # type: ignore[assignment]
    scan_overlaps = None  # type: ignore[assignment]
    scan_internal_gaps = None  # type: ignore[assignment]
    scan_slivers = None  # type: ignore[assignment]
    add_or_replace_error_layer_in_project = None  # type: ignore[assignment]
    fix_ring_self_intersections = None  # type: ignore[assignment]
    fix_overlaps = None  # type: ignore[assignment]
    fix_internal_gaps = None  # type: ignore[assignment]
    fix_slivers = None  # type: ignore[assignment]


@dataclasses.dataclass
class OperationSummary:
    """Normalized scan/fix operation summary for UI tables and Processing outputs."""

    section: str
    action: str
    count: int
    total_area_m2: float
    status: str
    messages: list[str]
    layer: Any = None


def ensure_core_available() -> None:
    """Verify vector_data_office_boy.core was imported cleanly."""
    if _CORE_IMPORT_ERROR is not None:
        raise RuntimeError(
            "The core spatial topology engine (vector_data_office_boy.core) is not available: "
            f"{_CORE_IMPORT_ERROR}"
        ) from _CORE_IMPORT_ERROR


def _get_field_names_and_defaults(cls: type[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inspect accepted parameter names, default values, and type hints for a config class."""
    defaults: dict[str, Any] = {}
    hints: dict[str, Any] = {}
    try:
        hints = get_type_hints(cls)
    except Exception:
        hints = {}

    if dataclasses.is_dataclass(cls):
        for field in dataclasses.fields(cls):
            if field.default is not dataclasses.MISSING:
                defaults[field.name] = field.default
            elif field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                defaults[field.name] = field.default_factory()
            else:
                defaults[field.name] = inspect.Parameter.empty
        return defaults, hints

    sig = inspect.signature(cls.__init__)
    for name, param in sig.parameters.items():
        if name == "self" or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        defaults[name] = param.default
    return defaults, hints


def _match_keywords(candidate_text: str, keywords: tuple[str, ...]) -> int:
    """Score how strongly a candidate string matches expected rule keywords."""
    lowered = candidate_text.strip().lower()
    for rank, keyword in enumerate(keywords):
        if lowered == keyword:
            return 100 - rank
    for rank, keyword in enumerate(keywords):
        if keyword in lowered:
            return 50 - rank
    return 0


def _resolve_rule_value(
    section: str,
    rule_index: int,
    labels: tuple[str, ...],
    default_val: Any,
    hint: Any,
) -> Any:
    """Resolve an integer UI rule index into the concrete value expected by core."""
    clamped_idx = max(0, min(rule_index, len(labels) - 1))
    keywords_tuple = _RULE_KEYWORDS[section]
    target_keywords = keywords_tuple[clamped_idx]

    enum_cls: type[Enum] | None = None
    if isinstance(hint, type) and issubclass(hint, Enum):
        enum_cls = hint
    elif isinstance(default_val, Enum):
        enum_cls = type(default_val)

    if enum_cls is not None:
        members = list(enum_cls)
        best_member = None
        best_score = 0
        for member in members:
            score = max(
                _match_keywords(member.name, target_keywords),
                _match_keywords(str(member.value), target_keywords),
            )
            if score > best_score:
                best_score = score
                best_member = member
        if best_member is not None:
            return best_member
        if clamped_idx < len(members):
            return members[clamped_idx]

    if get_origin(hint) is Literal:
        literal_args = list(get_args(hint))
        best_arg = None
        best_score = 0
        for arg in literal_args:
            score = _match_keywords(str(arg), target_keywords)
            if score > best_score:
                best_score = score
                best_arg = arg
        if best_arg is not None:
            return best_arg
        if clamped_idx < len(literal_args):
            return literal_args[clamped_idx]

    if isinstance(default_val, int) and not isinstance(default_val, bool):
        return clamped_idx

    if isinstance(default_val, str):
        if default_val == labels[0] or " " in default_val:
            return labels[clamped_idx]
        if "option_" in default_val.lower():
            return ("option_a", "option_b", "option_c")[clamped_idx]
        if core_models is not None:
            valid_list_map = {
                "overlap": "VALID_OVERLAP_RULES",
                "gap": "VALID_GAP_MERGE_RULES",
                "sliver": "VALID_SLIVER_MERGE_RULES",
            }
            valid_rules = getattr(core_models, valid_list_map.get(section, ""), None)
            if isinstance(valid_rules, (tuple, list)) and clamped_idx < len(valid_rules):
                return valid_rules[clamped_idx]

            best_const = None
            best_score = 0
            for attr_name, attr_val in vars(core_models).items():
                if not isinstance(attr_val, str) or attr_name.startswith("_"):
                    continue
                if attr_name.startswith("ERROR_"):
                    continue
                score = max(
                    _match_keywords(attr_val, target_keywords),
                    _match_keywords(attr_name, target_keywords),
                )
                if score > best_score:
                    best_score = score
                    best_const = attr_val
            if best_const is not None:
                return best_const

    return _RULE_FALLBACK_SLUGS[section][clamped_idx]


def _assign_first_matching_field(
    kwargs: dict[str, Any],
    available_fields: dict[str, Any],
    candidates: tuple[str, ...],
    value: Any,
) -> None:
    """Assign value to the first matching field name present on the config class."""
    for name in candidates:
        if name in available_fields:
            kwargs[name] = value
            return


def build_self_intersection_config(
    prune_spikes: bool = True,
    spike_angle: float = 1.0,
    recalc_attrs: bool = True,
) -> Any:
    """Construct SelfIntersectionConfig compatible with core.models."""
    ensure_core_available()
    fields, _ = _get_field_names_and_defaults(SelfIntersectionConfig)
    kwargs: dict[str, Any] = {}
    _assign_first_matching_field(
        kwargs,
        fields,
        ("detect_spikes", "prune_spikes", "enable_spike_pruning", "remove_spikes"),
        bool(prune_spikes),
    )
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "max_spike_angle_deg",
            "spike_angle_deg",
            "spike_angle",
            "min_spike_angle_deg",
            "min_spike_angle",
            "angle_threshold_deg",
            "angle_threshold",
        ),
        float(spike_angle),
    )
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "recalc_attributes",
            "recalc_attrs",
            "recalculate_attributes",
            "recalculate_fields",
            "update_area_fields",
        ),
        bool(recalc_attrs),
    )
    return SelfIntersectionConfig(**kwargs)


def build_overlap_config(
    rule_index: int = 0,
    min_overlap_area: float = 0.0001,
    include_micro_overlap: bool = False,
    recalc_attrs: bool = True,
) -> Any:
    """Construct OverlapConfig compatible with core.models."""
    ensure_core_available()
    fields, hints = _get_field_names_and_defaults(OverlapConfig)
    kwargs: dict[str, Any] = {}
    for rule_field in ("overlap_rule", "rule", "resolution_rule", "strategy", "mode"):
        if rule_field in fields:
            kwargs[rule_field] = _resolve_rule_value(
                "overlap",
                rule_index,
                OVERLAP_RULE_LABELS,
                fields[rule_field],
                hints.get(rule_field),
            )
            break
    _assign_first_matching_field(
        kwargs,
        fields,
        ("min_overlap_area_m2", "min_overlap_area", "min_area_m2", "min_area", "area_threshold_m2"),
        float(min_overlap_area),
    )
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "include_micro_overlaps",
            "include_micro_overlap",
            "include_micro",
            "allow_micro_overlaps",
        ),
        bool(include_micro_overlap),
    )
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "recalc_attributes",
            "recalc_attrs",
            "recalculate_attributes",
            "recalculate_fields",
            "update_area_fields",
        ),
        bool(recalc_attrs),
    )
    return OverlapConfig(**kwargs)


def build_gap_config(
    rule_index: int = 0,
    recalc_attrs: bool = True,
) -> Any:
    """Construct GapConfig compatible with core.models."""
    ensure_core_available()
    fields, hints = _get_field_names_and_defaults(GapConfig)
    kwargs: dict[str, Any] = {}
    for rule_field in ("merge_rule", "rule", "gap_rule", "strategy", "mode"):
        if rule_field in fields:
            kwargs[rule_field] = _resolve_rule_value(
                "gap",
                rule_index,
                GAP_RULE_LABELS,
                fields[rule_field],
                hints.get(rule_field),
            )
            break
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "recalc_attributes",
            "recalc_attrs",
            "recalculate_attributes",
            "recalculate_fields",
            "update_area_fields",
        ),
        bool(recalc_attrs),
    )
    return GapConfig(**kwargs)


def build_sliver_config(
    sliver_area: float = 10.0,
    sliver_pp: float = 0.055,
    sliver_max_area_pp: float = 150.0,
    part_min_area: float = 1.0,
    rule_index: int = 0,
    recalc_attrs: bool = True,
) -> Any:
    """Construct SliverConfig compatible with core.models."""
    ensure_core_available()
    fields, hints = _get_field_names_and_defaults(SliverConfig)
    kwargs: dict[str, Any] = {}
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "max_micro_sliver_area_m2",
            "sliver_area_m2",
            "max_sliver_area_m2",
            "sliver_area",
            "area_threshold_m2",
            "max_area_m2",
        ),
        float(sliver_area),
    )
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "min_polsby_popper",
            "sliver_pp_threshold",
            "pp_threshold",
            "sliver_pp",
            "polsby_popper_threshold",
            "max_pp",
        ),
        float(sliver_pp),
    )
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "max_thin_sliver_area_m2",
            "sliver_max_area_pp_m2",
            "pp_max_area_m2",
            "sliver_max_area_pp",
            "max_area_for_pp_m2",
            "max_pp_area_m2",
        ),
        float(sliver_max_area_pp),
    )
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "min_multipart_area_m2",
            "part_min_area_m2",
            "min_part_area_m2",
            "part_min_area",
            "min_part_area",
        ),
        float(part_min_area),
    )
    for rule_field in ("merge_rule", "rule", "sliver_rule", "strategy", "mode"):
        if rule_field in fields:
            kwargs[rule_field] = _resolve_rule_value(
                "sliver",
                rule_index,
                SLIVER_RULE_LABELS,
                fields[rule_field],
                hints.get(rule_field),
            )
            break
    _assign_first_matching_field(
        kwargs,
        fields,
        (
            "recalc_attributes",
            "recalc_attrs",
            "recalculate_attributes",
            "recalculate_fields",
            "update_area_fields",
        ),
        bool(recalc_attrs),
    )
    return SliverConfig(**kwargs)


def invoke_core_operation(
    fn: Callable[..., Any],
    layer: Any,
    config: Any,
    *,
    recalc_attrs: bool | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> Any:
    """Invoke a core scan_* or fix_* function adapting smoothly to its signature."""
    ensure_core_available()
    sig = inspect.signature(fn)
    params = sig.parameters
    positional_args: list[Any] = []
    keyword_args: dict[str, Any] = {}

    layer_param = next(
        (name for name in ("layer", "vector_layer", "input_layer", "qgs_layer") if name in params),
        None,
    )
    if layer_param is not None:
        keyword_args[layer_param] = layer
    else:
        positional_args.append(layer)

    config_param = next(
        (name for name in ("config", "cfg", "params", "options", "settings") if name in params),
        None,
    )
    if config_param is not None:
        keyword_args[config_param] = config
    elif len(params) >= 2 and not layer_param:
        positional_args.append(config)

    if recalc_attrs is not None:
        for attr_param in (
            "recalc_attributes",
            "recalc_attrs",
            "recalculate_attributes",
            "recalculate_fields",
            "update_area_fields",
        ):
            if attr_param in params:
                keyword_args[attr_param] = bool(recalc_attrs)
                break

    if progress_callback is not None:
        for cb_param in ("progress_callback", "callback"):
            if cb_param in params:
                keyword_args[cb_param] = progress_callback
                break

    return fn(*positional_args, **keyword_args)


def register_error_layer(scan_result: Any, project: Any, iface: Any = None) -> Any:
    """Call add_or_replace_error_layer_in_project matching its parameter signature."""
    ensure_core_available()
    sig = inspect.signature(add_or_replace_error_layer_in_project)
    if len(sig.parameters) >= 3 or "iface" in sig.parameters:
        return add_or_replace_error_layer_in_project(scan_result, project, iface)
    return add_or_replace_error_layer_in_project(scan_result, project)


def _read_first_attr(obj: Any, candidates: tuple[str, ...], default: Any = None) -> Any:
    """Return the first present non-None attribute from candidate names."""
    if isinstance(obj, dict):
        for name in candidates:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in candidates:
        if hasattr(obj, name):
            val = getattr(obj, name)
            if val is not None:
                return val
    return default


def _collect_messages(obj: Any) -> list[str]:
    """Extract normalized log messages/warnings from a core result object."""
    messages: list[str] = []
    for attr in ("message", "summary", "detail", "details"):
        val = _read_first_attr(obj, (attr,))
        if isinstance(val, str) and val.strip():
            messages.append(val.strip())
        elif isinstance(val, (list, tuple)):
            messages.extend(str(item).strip() for item in val if str(item).strip())
    for attr in ("warnings", "messages", "notes", "logs"):
        val = _read_first_attr(obj, (attr,))
        if isinstance(val, (list, tuple)):
            messages.extend(str(item).strip() for item in val if str(item).strip())
    return messages


def extract_operation_summary(
    result: Any,
    *,
    section: str,
    action: str,
) -> OperationSummary:
    """Normalize a ScanResult or FixResult into an OperationSummary."""
    layer = _read_first_attr(
        result,
        ("memory_layer", "error_layer", "output_layer", "reloaded_layer", "layer"),
    )
    count_val = _read_first_attr(
        result,
        (
            "error_count",
            "fixed_count",
            "repaired_count",
            "resolved_count",
            "count",
            "num_errors",
            "num_fixed",
            "total_count",
        ),
    )
    if count_val is None:
        errors_seq = _read_first_attr(result, ("errors", "features", "items"))
        if errors_seq is not None and hasattr(errors_seq, "__len__"):
            count_val = len(errors_seq)
        elif layer is not None and hasattr(layer, "featureCount"):
            count_val = layer.featureCount()
        else:
            count_val = 0

    area_val = _read_first_attr(
        result,
        (
            "total_area_m2",
            "area_changed_m2",
            "area_m2",
            "total_area",
            "fixed_area_m2",
            "error_area_m2",
            "resolved_area_m2",
        ),
        0.0,
    )
    messages = _collect_messages(result)
    explicit_status = _read_first_attr(result, ("status",))
    if isinstance(explicit_status, str) and explicit_status.strip():
        status = explicit_status.strip()
    elif action == "Scan":
        status = "Clean (0 errors)" if int(count_val) == 0 else f"Found {int(count_val)} error(s)"
    else:
        remaining = _read_first_attr(result, ("remaining_errors",))
        before = _read_first_attr(result, ("features_before",))
        after = _read_first_attr(result, ("features_after",))
        if before is not None and after is not None and remaining is not None:
            status = (
                f"Repaired {int(count_val)} (features: {before}->{after}, "
                f"remaining: {remaining})"
            )
        elif int(count_val) == 0:
            status = "No changes needed"
        else:
            status = f"Repaired {int(count_val)} item(s)"

    return OperationSummary(
        section=section,
        action=action,
        count=int(count_val),
        total_area_m2=float(area_val),
        status=status,
        messages=messages,
        layer=layer,
    )
