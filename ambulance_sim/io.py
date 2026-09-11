"""Load and save local, JSON-based :class:`~ambulance_sim.model.Scenario` data.

The simulator deliberately keeps its input boundary small.  A scenario is a
single UTF-8 JSON document, which makes a data hand-off reproducible and easy
to inspect in version control.  Unknown fields are ignored when loading so a
future version of the model can add fields without making older input files
unusable.  Fields that are optional in the current model have documented
defaults; malformed values raise :class:`ScenarioValidationError` with the
JSON path of the offending value.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from .model import (
    Ambulance,
    AmbulanceStatus,
    Hospital,
    PatientProfile,
    RoadNetwork,
    Scenario,
)


class ScenarioValidationError(ValueError):
    """Raised when a JSON scenario cannot be converted to a valid Scenario."""


_MISSING = object()


def _fail(path: str, message: str) -> None:
    raise ScenarioValidationError(f"{path}: {message}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    return value


def _string(value: Any, path: str, *, default: str | None = None) -> str:
    if value is _MISSING:
        if default is not None:
            return default
        _fail(path, "is required")
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string")
    return value


def _number(
    value: Any,
    path: str,
    *,
    default: float | int | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
) -> float | int:
    if value is _MISSING:
        if default is not None:
            value = default
        else:
            _fail(path, "is required")
    # bool is a subclass of int, but is never a useful duration/rate here.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "must be a number")
    if not math.isfinite(float(value)):
        _fail(path, "must be finite")
    if integer and (isinstance(value, float) and not value.is_integer()):
        _fail(path, "must be an integer")
    if minimum is not None and value < minimum:
        _fail(path, f"must be >= {minimum}")
    if maximum is not None and value > maximum:
        _fail(path, f"must be <= {maximum}")
    return int(value) if integer else float(value)


def _boolean(value: Any, path: str, *, default: bool) -> bool:
    if value is _MISSING:
        return default
    if not isinstance(value, bool):
        _fail(path, "must be true or false")
    return value


def _list_of_strings(value: Any, path: str, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is _MISSING or value is None:
        return default
    if not isinstance(value, (list, tuple)):
        _fail(path, "must be an array of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_string(item, f"{path}[{index}]"))
    return tuple(result)


def _records(value: Any, path: str) -> list[tuple[str | None, Mapping[str, Any], str]]:
    """Normalize either an id-keyed object or an array of records.

    The object form is convenient for hand-authored files; the array form is
    convenient when exporting rows from a table.  Both forms produce the same
    model.
    """
    if value is _MISSING or value is None:
        return []
    result: list[tuple[str | None, Mapping[str, Any], str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if not isinstance(key, str) or not key.strip():
                _fail(path, "record keys must be non-empty strings")
            record = {} if item is None else _mapping(item, item_path)
            result.append((key, record, item_path))
        return result
    if isinstance(value, list):
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            result.append((None, _mapping(item, item_path), item_path))
        return result
    _fail(path, "must be an object keyed by id or an array of records")


def _record_id(key: str | None, record: Mapping[str, Any], path: str, label: str) -> str:
    value = record.get("id", _MISSING)
    if value is _MISSING and label == "profile":
        value = record.get("name", _MISSING)
    if value is _MISSING and key is not None:
        value = key
    return _string(value, f"{path}.id")


def _edges(raw: Any, path: str = "network.edges") -> dict[str, dict[str, float]]:
    if raw is _MISSING or raw is None:
        return {}
    outer = _mapping(raw, path)
    result: dict[str, dict[str, float]] = {}
    for source, neighbors in outer.items():
        if not isinstance(source, str) or not source.strip():
            _fail(path, "node names must be non-empty strings")
        neighbor_map = _mapping(neighbors, f"{path}.{source}")
        result[source] = {}
        for target, duration in neighbor_map.items():
            if not isinstance(target, str) or not target.strip():
                _fail(f"{path}.{source}", "neighbor names must be non-empty strings")
            result[source][target] = float(_number(duration, f"{path}.{source}.{target}", minimum=0))
    return result


def _normalise_network(raw: Any) -> dict[str, dict[str, float]]:
    if raw is _MISSING or raw is None:
        return {}
    network = _mapping(raw, "network")
    # Accept a bare edge map as a small convenience in addition to
    # {"edges": {...}}.  A map containing "edges" is always treated as the
    # wrapped form.
    if "edges" in network:
        return _edges(network["edges"])
    return _edges(network, "network")


def _hour_key(value: Any, path: str) -> int:
    # JSON object keys are strings, while the model uses integer hours.
    if isinstance(value, bool):
        _fail(path, "hour must be an integer from 0 to 23")
    try:
        hour = int(value)
    except (TypeError, ValueError):
        _fail(path, "hour must be an integer from 0 to 23")
    if str(value).strip() not in {str(hour), f"{hour:02d}"} and not isinstance(value, int):
        _fail(path, "hour must be an integer from 0 to 23")
    if hour < 0 or hour > 23:
        _fail(path, "hour must be an integer from 0 to 23")
    return hour


def _hourly_values(
    raw: Any,
    path: str,
    *,
    minimum: float = 0.0,
) -> dict[int, dict[str, float]]:
    if raw is _MISSING or raw is None:
        return {}
    outer = _mapping(raw, path)
    result: dict[int, dict[str, float]] = {}
    for raw_hour, row in outer.items():
        hour = _hour_key(raw_hour, f"{path}.{raw_hour}")
        values = _mapping(row, f"{path}.{raw_hour}")
        result[hour] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not key.strip():
                _fail(f"{path}.{raw_hour}", "keys must be non-empty strings")
            result[hour][key] = float(_number(value, f"{path}.{raw_hour}.{key}", minimum=minimum))
    return result


def _time_multipliers(raw: Any, path: str = "network.time_multipliers") -> dict[int, float]:
    if raw is _MISSING or raw is None:
        return {}
    values = _mapping(raw, path)
    result: dict[int, float] = {}
    for raw_hour, value in values.items():
        hour = _hour_key(raw_hour, f"{path}.{raw_hour}")
        result[hour] = float(_number(value, f"{path}.{raw_hour}", minimum=0.0000000001))
    return result


def _positions(raw: Any, path: str = "network.positions") -> dict[str, tuple[float, float]]:
    if raw is _MISSING or raw is None:
        return {}
    values = _mapping(raw, path)
    result: dict[str, tuple[float, float]] = {}
    for node, coordinates in values.items():
        if not isinstance(node, str) or not node.strip():
            _fail(path, "node names must be non-empty strings")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2:
            _fail(f"{path}.{node}", "must be a two-number [x, y] array")
        result[node] = (
            float(_number(coordinates[0], f"{path}.{node}[0]")),
            float(_number(coordinates[1], f"{path}.{node}[1]")),
        )
    return result


def _probability_map(raw: Any, path: str) -> dict[str, float]:
    if raw is _MISSING or raw is None:
        return {}
    values = _mapping(raw, path)
    result: dict[str, float] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name.strip():
            _fail(path, "profile names must be non-empty strings")
        result[name] = float(_number(value, f"{path}.{name}", minimum=0, maximum=1))
    return result


def scenario_from_dict(data: Mapping[str, Any], *, source: str = "scenario") -> Scenario:
    """Build and validate a :class:`Scenario` from a JSON-compatible mapping.

    Missing model fields use safe defaults: empty collections, a 24-hour
    horizon, and two permitted transfers.  A scenario with no patient
    profiles is valid for an empty-demand smoke test, but a non-empty demand
    stream requires at least one profile.
    """
    root = _mapping(data, source)
    raw_network = root.get("network", _MISSING)
    network = RoadNetwork(
        _normalise_network(raw_network),
        time_multipliers=(
            _time_multipliers(_mapping(raw_network, "network").get("time_multipliers", _MISSING))
            if raw_network is not _MISSING and raw_network is not None
            else {}
        ),
        positions=(
            _positions(_mapping(raw_network, "network").get("positions", _MISSING))
            if raw_network is not _MISSING and raw_network is not None
            else {}
        ),
    )

    villages_raw = root.get("villages", _MISSING)
    villages_map = {} if villages_raw in (_MISSING, None) else _mapping(villages_raw, "villages")
    villages: dict[str, float] = {}
    for village, rate in villages_map.items():
        if not isinstance(village, str) or not village.strip():
            _fail("villages", "village names must be non-empty strings")
        villages[village] = float(_number(rate, f"villages.{village}", minimum=0))

    hospitals: dict[str, Hospital] = {}
    for key, record, path in _records(root.get("hospitals", _MISSING), "hospitals"):
        ident = _record_id(key, record, path, "hospital")
        if ident in hospitals:
            _fail(f"{path}.id", f"duplicate id {ident!r}")
        location = _string(record.get("location", _MISSING), f"{path}.location")
        capabilities = set(_list_of_strings(record.get("capabilities", _MISSING), f"{path}.capabilities"))
        hospitals[ident] = Hospital(
            id=ident,
            location=location,
            capabilities=capabilities,
            success_when_available=float(_number(record.get("success_when_available", _MISSING), f"{path}.success_when_available", default=0.0, minimum=0, maximum=1)),
            success_when_unavailable=float(_number(record.get("success_when_unavailable", _MISSING), f"{path}.success_when_unavailable", default=0.0, minimum=0, maximum=1)),
            available=_boolean(record.get("available", _MISSING), f"{path}.available", default=True),
            restock_capable=_boolean(record.get("restock_capable", _MISSING), f"{path}.restock_capable", default=True),
            transfer_delay_minutes=float(_number(record.get("transfer_delay_minutes", _MISSING), f"{path}.transfer_delay_minutes", default=5.0, minimum=0)),
            capacity=(
                None
                if record.get("capacity", _MISSING) in (_MISSING, None)
                else int(_number(record["capacity"], f"{path}.capacity", minimum=0, integer=True))
            ),
            occupied=int(_number(record.get("occupied", _MISSING), f"{path}.occupied", default=0, minimum=0, integer=True)),
            treatment_minutes=float(_number(record.get("treatment_minutes", _MISSING), f"{path}.treatment_minutes", default=0.0, minimum=0)),
            success_by_profile=_probability_map(
                record.get("success_by_profile", _MISSING), f"{path}.success_by_profile"
            ),
            unavailable_success_by_profile=_probability_map(
                record.get("unavailable_success_by_profile", _MISSING),
                f"{path}.unavailable_success_by_profile",
            ),
        )

    ambulances: dict[str, Ambulance] = {}
    for key, record, path in _records(root.get("ambulances", _MISSING), "ambulances"):
        ident = _record_id(key, record, path, "ambulance")
        if ident in ambulances:
            _fail(f"{path}.id", f"duplicate id {ident!r}")
        location = _string(record.get("location", _MISSING), f"{path}.location")
        status_value = record.get("status", AmbulanceStatus.IDLE.value)
        try:
            status = AmbulanceStatus(status_value)
        except (TypeError, ValueError):
            allowed = ", ".join(item.value for item in AmbulanceStatus)
            _fail(f"{path}.status", f"must be one of: {allowed}")
        patient_id = record.get("patient_id", None)
        if patient_id is not None and (not isinstance(patient_id, str) or not patient_id.strip()):
            _fail(f"{path}.patient_id", "must be a string or null")
        optional_nodes: dict[str, str | None] = {}
        for key in ("home_base", "assigned_post"):
            value = record.get(key, None)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                _fail(f"{path}.{key}", "must be a string or null")
            optional_nodes[key] = value
        ambulances[ident] = Ambulance(
            id=ident,
            location=location,
            status=status,
            patient_id=patient_id,
            restock_minutes=float(_number(record.get("restock_minutes", _MISSING), f"{path}.restock_minutes", default=8.0, minimum=0)),
            initial_available_after=float(_number(
                record.get("initial_available_after", _MISSING),
                f"{path}.initial_available_after",
                default=0.0,
                minimum=0,
            )),
            home_base=optional_nodes["home_base"],
            assigned_post=optional_nodes["assigned_post"],
        )

    profiles: list[PatientProfile] = []
    profile_names: set[str] = set()
    for key, record, path in _records(root.get("profiles", _MISSING), "profiles"):
        name = _record_id(key, record, path, "profile")
        if name in profile_names:
            _fail(f"{path}.name", f"duplicate profile name {name!r}")
        profile_names.add(name)
        profiles.append(
            PatientProfile(
                name=name,
                golden_minutes=float(_number(record.get("golden_minutes", _MISSING), f"{path}.golden_minutes", default=0.0, minimum=0)),
                decay_rate=float(_number(record.get("decay_rate", _MISSING), f"{path}.decay_rate", default=0.0, minimum=0)),
                scene_minutes=float(_number(record.get("scene_minutes", _MISSING), f"{path}.scene_minutes", default=0.0, minimum=0)),
                field_success=float(_number(record.get("field_success", _MISSING), f"{path}.field_success", default=0.0, minimum=0, maximum=1)),
            )
        )

    standby_value = root.get("standby_nodes", _MISSING)
    if standby_value is _MISSING or standby_value is None:
        # Hospitals are useful, valid fallback standby points when a source
        # file does not yet contain station data.
        standby_nodes = tuple(dict.fromkeys(h.location for h in hospitals.values()))
    else:
        standby_nodes = _list_of_strings(standby_value, "standby_nodes")

    horizon = float(_number(root.get("horizon_minutes", _MISSING), "horizon_minutes", default=24 * 60, minimum=0))
    max_transfers = int(_number(root.get("max_transfers", _MISSING), "max_transfers", default=2, minimum=0, integer=True))
    raw_cutoff = root.get("arrival_cutoff_minutes", _MISSING)
    arrival_cutoff = (
        None if raw_cutoff is _MISSING or raw_cutoff is None
        else float(_number(raw_cutoff, "arrival_cutoff_minutes", default=horizon, minimum=0))
    )
    hourly_village_rates = _hourly_values(root.get("hourly_village_rates", _MISSING), "hourly_village_rates")
    hourly_profile_probabilities = _hourly_values(root.get("hourly_profile_probabilities", _MISSING), "hourly_profile_probabilities")
    scenario = Scenario(
        network=network,
        villages=villages,
        hospitals=hospitals,
        ambulances=ambulances,
        profiles=tuple(profiles),
        standby_nodes=standby_nodes,
        horizon_minutes=horizon,
        max_transfers=max_transfers,
        hourly_village_rates=hourly_village_rates,
        hourly_profile_probabilities=hourly_profile_probabilities,
        arrival_cutoff_minutes=arrival_cutoff,
    )
    validate_scenario(scenario, source=source)
    return scenario


def validate_scenario(scenario: Scenario, *, source: str = "scenario") -> None:
    """Validate a constructed Scenario, raising a path-aware error."""
    if not isinstance(scenario, Scenario):
        _fail(source, "must be a Scenario instance")
    # Check references against the graph, but permit a completely empty
    # graph for zero-demand/unit-test scenarios.
    graph_nodes = set(scenario.network.edges)
    graph_nodes.update(target for neighbors in scenario.network.edges.values() for target in neighbors)
    references: list[tuple[str, str]] = []
    demand_nodes = set(scenario.villages)
    for rates in scenario.hourly_village_rates.values():
        demand_nodes.update(rates)
    references.extend((f"demand.{node}", node) for node in demand_nodes)
    references.extend((f"hospitals.{ident}.location", hospital.location) for ident, hospital in scenario.hospitals.items())
    references.extend((f"ambulances.{ident}.location", ambulance.location) for ident, ambulance in scenario.ambulances.items())
    for ident, ambulance in scenario.ambulances.items():
        for key, node in (("home_base", ambulance.home_base), ("assigned_post", ambulance.assigned_post)):
            if node is not None:
                references.append((f"ambulances.{ident}.{key}", node))
    references.extend((f"standby_nodes[{index}]", node) for index, node in enumerate(scenario.standby_nodes))
    if graph_nodes:
        for path, node in references:
            if node not in graph_nodes:
                _fail(path, f"node {node!r} is not present in network.edges")
    has_positive_demand = any(rate > 0 for rate in scenario.villages.values()) or any(
        rate > 0 for row in scenario.hourly_village_rates.values() for rate in row.values()
    )
    if has_positive_demand and not scenario.profiles:
        _fail("profiles", "at least one patient profile is required when villages are configured")
    if scenario.horizon_minutes < 0 or not math.isfinite(scenario.horizon_minutes):
        _fail("horizon_minutes", "must be a finite number >= 0")
    cutoff = scenario.arrival_cutoff_minutes
    if cutoff is not None and (not math.isfinite(cutoff) or not 0 <= cutoff <= scenario.horizon_minutes):
        _fail("arrival_cutoff_minutes", "must be a finite number between 0 and horizon_minutes")
    if scenario.max_transfers < 0:
        _fail("max_transfers", "must be an integer >= 0")
    for hour, multiplier in scenario.network.time_multipliers.items():
        if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
            _fail("network.time_multipliers", "hours must be integers from 0 to 23")
        if not math.isfinite(multiplier) or multiplier <= 0:
            _fail(f"network.time_multipliers.{hour}", "must be a finite number > 0")
    for node, coordinates in scenario.network.positions.items():
        if node not in graph_nodes:
            _fail(f"network.positions.{node}", "node is not present in network.edges")
        if len(coordinates) != 2 or not all(math.isfinite(value) for value in coordinates):
            _fail(f"network.positions.{node}", "must contain two finite coordinates")
    for path, rows in (
        ("hourly_village_rates", scenario.hourly_village_rates),
        ("hourly_profile_probabilities", scenario.hourly_profile_probabilities),
    ):
        for hour, values in rows.items():
            if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
                _fail(path, "hours must be integers from 0 to 23")
            for key, value in values.items():
                if not math.isfinite(value) or value < 0:
                    _fail(f"{path}.{hour}.{key}", "must be a finite number >= 0")
    for ident, hospital in scenario.hospitals.items():
        if hospital.capacity is not None and hospital.occupied > hospital.capacity:
            _fail(f"hospitals.{ident}.occupied", "cannot exceed capacity")
        unknown_success_profiles = (
            set(hospital.success_by_profile) | set(hospital.unavailable_success_by_profile)
        ) - set(hospital.capabilities)
        if unknown_success_profiles:
            _fail(
                f"hospitals.{ident}.success_by_profile",
                f"profiles must also appear in capabilities: {sorted(unknown_success_profiles)}",
            )
    profile_names = {profile.name for profile in scenario.profiles}
    for hour, weights in scenario.hourly_profile_probabilities.items():
        unknown = set(weights) - profile_names
        if unknown:
            _fail(f"hourly_profile_probabilities.{hour}", f"unknown profiles: {sorted(unknown)}")
    for ident, ambulance in scenario.ambulances.items():
        if ambulance.status not in {AmbulanceStatus.IDLE, AmbulanceStatus.UNAVAILABLE}:
            _fail(
                f"ambulances.{ident}.status",
                "initial JSON scenarios support only idle or unavailable; active patients are generated by the simulator",
            )
        if ambulance.status is AmbulanceStatus.IDLE and ambulance.initial_available_after > 0:
            _fail(
                f"ambulances.{ident}.initial_available_after",
                "requires status 'unavailable' when greater than zero",
            )


def scenario_to_dict(scenario: Scenario) -> dict[str, Any]:
    """Serialize a Scenario to a JSON-compatible mapping."""
    validate_scenario(scenario)
    return {
        "schema_version": 1,
        "network": {
            "edges": {source: dict(neighbors) for source, neighbors in scenario.network.edges.items()},
            "time_multipliers": dict(scenario.network.time_multipliers),
            "positions": {node: list(coordinates) for node, coordinates in scenario.network.positions.items()},
        },
        "villages": dict(scenario.villages),
        "hospitals": {
            ident: {
                "location": hospital.location,
                "capabilities": sorted(hospital.capabilities),
                "success_when_available": hospital.success_when_available,
                "success_when_unavailable": hospital.success_when_unavailable,
                "available": hospital.available,
                "restock_capable": hospital.restock_capable,
                "transfer_delay_minutes": hospital.transfer_delay_minutes,
                "capacity": hospital.capacity,
                "occupied": hospital.occupied,
                "treatment_minutes": hospital.treatment_minutes,
                "success_by_profile": dict(hospital.success_by_profile),
                "unavailable_success_by_profile": dict(hospital.unavailable_success_by_profile),
            }
            for ident, hospital in scenario.hospitals.items()
        },
        "ambulances": {
            ident: {
                "location": ambulance.location,
                "status": ambulance.status.value,
                "patient_id": ambulance.patient_id,
                "restock_minutes": ambulance.restock_minutes,
                "initial_available_after": ambulance.initial_available_after,
                "home_base": ambulance.home_base,
                "assigned_post": ambulance.assigned_post,
            }
            for ident, ambulance in scenario.ambulances.items()
        },
        "profiles": [
            {
                "name": profile.name,
                "golden_minutes": profile.golden_minutes,
                "decay_rate": profile.decay_rate,
                "scene_minutes": profile.scene_minutes,
                "field_success": profile.field_success,
            }
            for profile in scenario.profiles
        ],
        "standby_nodes": list(scenario.standby_nodes),
        "horizon_minutes": scenario.horizon_minutes,
        "max_transfers": scenario.max_transfers,
        "arrival_cutoff_minutes": scenario.arrival_cutoff_minutes,
        "hourly_village_rates": {
            str(hour): dict(values) for hour, values in scenario.hourly_village_rates.items()
        },
        "hourly_profile_probabilities": {
            str(hour): dict(values) for hour, values in scenario.hourly_profile_probabilities.items()
        },
    }


def load_scenario(path: str | Path) -> Scenario:
    """Load one UTF-8 JSON file and return its validated Scenario."""
    file_path = Path(path)
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ScenarioValidationError(f"{file_path}: file not found") from exc
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(f"{file_path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    except OSError as exc:
        raise ScenarioValidationError(f"{file_path}: cannot read file: {exc}") from exc
    return scenario_from_dict(data, source=str(file_path))


def save_scenario(scenario: Scenario, path: str | Path, *, indent: int = 2) -> None:
    """Validate and save a Scenario as UTF-8 JSON, creating parent folders."""
    if not isinstance(indent, int) or isinstance(indent, bool) or indent < 0:
        raise ValueError("indent must be a non-negative integer")
    file_path = Path(path)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(scenario_to_dict(scenario), handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
    except OSError as exc:
        raise ScenarioValidationError(f"{file_path}: cannot write file: {exc}") from exc


__all__ = [
    "ScenarioValidationError",
    "load_scenario",
    "save_scenario",
    "scenario_from_dict",
    "scenario_to_dict",
    "validate_scenario",
]
