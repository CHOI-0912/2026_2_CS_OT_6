"""Build a Folium SMDP playback of the optimizer's own municipal inputs.

Two input modes share one playback:

``--placement placement.json``
    Hospital-placement mode (kept for reference).  Candidate sets are activated
    exactly the way ``ambulance_sim.hospital_placement`` does.

``--standby standby.json``
    Ambulance-repositioning mode.  Standby posts are read from the manifest and
    an assignment from the optimizer result is applied by setting each
    ambulance's ``assigned_post``, which is what ``ScheduledStandbyPolicy``
    consumes.

The optimizer inputs are the single source of truth in both modes: this script
never rebuilds a Scenario from CSVs and never invents coefficients, so the
playback shown in the browser is the same episode the optimizer scored
(identical seed -> identical trace).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import csv
from dataclasses import dataclass
from html import escape
import json
import math
from pathlib import Path
import sys

import folium
from branca.element import Element
from folium.plugins import Fullscreen, HeatMap, MeasureControl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.engine import Simulation
from ambulance_sim.hospital_placement import (
    CandidateHospital,
    MunicipalPlacementProblem,
    load_municipal_placement_problem,
)
from ambulance_sim.io import load_scenario, scenario_from_dict, scenario_to_dict
from ambulance_sim.model import Scenario
from ambulance_sim.policy import GreedySurvivalPolicy

AMBULANCE_STATUS = (
    "idle", "unavailable", "to_scene", "on_scene", "to_hospital", "restocking", "repositioning",
)
PATIENT_STATUS = ("waiting", "assigned", "on_scene", "transporting", "complete", "lost")
FRAME_EVENTS = (
    "initial", "initial_available", "patient_arrival", "arrive_scene", "scene_complete",
    "arrive_hospital", "arrive_mortuary", "hospital_release", "transfer_ready",
    "restock_complete", "reposition_complete", "shift_change", "horizon_end",
)
# Korean log labels; the index is what the embedded payload stores.
LOG_KINDS = (
    "환자 발생", "출동", "현장 도착", "현장 처치 완료", "이송",
    "병원 인계", "전원 준비", "사망 이송", "재정비", "재배치",
    "교대 시각: 대기지 이동/복귀",
)
LOG_FROM_EVENT = {
    "patient_arrival": 0, "arrive_scene": 2, "scene_complete": 3, "arrive_hospital": 5,
    "transfer_ready": 6, "arrive_mortuary": 7, "restock_complete": 8, "reposition_complete": 9,
    "shift_change": 10,
}
PROVISIONAL_WARNING = "임시 계수 사용 중 — 절대값을 인용하지 말 것"
POLICY_LABEL = "centralized greedy expected-survival"
STANDBY_POLICY_LABEL = "scheduled standby posts + centralized greedy expected-survival"
SCHEDULE_RULE = "07~24시 자유 배치 / 01~06시 원소속 복귀(최하위 우선순위)"
# The hours the rule sentence above describes; a manifest that disagrees is flagged
# on screen instead of being silently described by the wrong sentence.
RULE_HOME_HOURS = frozenset(range(1, 7))
POST_TYPE_LABELS = {"grid_cell": "격자 칸", "existing_base": "기존 119안전센터"}
# One colour per ambulance, reused by the assigned-post markers, the home -> post
# lines on the map, and the swatches in the results table.
AMBULANCE_PALETTE = (
    "#ef4444", "#3b82f6", "#22c55e", "#eab308", "#a855f7", "#06b6d4",
    "#f97316", "#ec4899", "#84cc16", "#14b8a6", "#6366f1", "#f43f5e",
)


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise ValueError(f"{path}: required municipality CSV is missing")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def coordinate_converter(provenance: dict) -> Callable[[str, tuple[float, float]], tuple[float, float]]:
    """Return a converter for network.positions using the documented order."""
    documented = str(provenance.get("coordinate_order", "")).lower()
    if "[longitude, latitude]" in documented or "[lon, lat]" in documented:
        order = "lonlat"
    elif "[latitude, longitude]" in documented or "[lat, lon]" in documented:
        order = "latlon"
    else:
        raise ValueError(
            "placement.json provenance.coordinate_order must document whether "
            "network.positions are [longitude, latitude] or [latitude, longitude]"
        )

    def convert(node: str, position: tuple[float, float]) -> tuple[float, float]:
        latitude, longitude = (position[1], position[0]) if order == "lonlat" else position
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            raise ValueError(f"{node}: position {position} is not a WGS84 coordinate")
        return latitude, longitude

    return convert


def municipality_folder(processed_dir: Path, code: str) -> Path:
    candidates = [processed_dir / code, processed_dir]
    for folder in candidates:
        if (folder / "demand_population.csv").exists():
            return folder
    raise ValueError(f"{processed_dir}: no demand_population.csv for municipality {code}")


def node_labels(
    scenario: Scenario,
    code: str,
    folder: Path,
    raw_scenario: dict,
    *,
    candidates: tuple[CandidateHospital, ...] = (),
    posts: tuple["StandbyPostView", ...] = (),
) -> dict[str, dict]:
    """Human-readable metadata per graph node, joined from the municipality CSVs.

    Existing-hospital identity prefers ``scenario.json``'s official_identifiers,
    which travel with the optimizer input, and falls back to the CSV row.
    """
    labels: dict[str, dict] = {}

    demand_rows = read_rows(folder / "demand_population.csv")
    demand_by_code = {row["administrative_code"].strip(): row for row in demand_rows}
    for node in scenario.villages:
        administrative_code = node.rsplit("::", 1)[-1]
        row = demand_by_code.get(administrative_code)
        if row is None:
            raise ValueError(f"{node}: no demand_population.csv row for {administrative_code}")
        labels[node] = {
            "kind": "demand",
            "name": row["administrative_name"],
            "population": int(row["population"]),
            "detail": row.get("matched_address") or "",
        }

    base_rows = read_rows(folder / "ambulance_bases.csv")
    hospital_rows = read_rows(folder / "existing_hospitals.csv")
    raw_hospitals = dict(raw_scenario.get("hospitals", {}))
    for ident, hospital in scenario.hospitals.items():
        node = hospital.location
        official = dict(raw_hospitals.get(ident, {}).get("official_identifiers", {}))
        index = node.rsplit("::", 1)[-1]
        row = hospital_rows[int(index) - 1] if index.isdigit() and int(index) <= len(hospital_rows) else {}
        labels[node] = {
            "kind": "hospital",
            "name": official.get("hospital_name") or row.get("hospital_name") or ident,
            "category": official.get("emergency_category") or row.get("emergency_category") or "",
            "beds": hospital.capacity,
            "critical_care_beds": row.get("critical_care_beds") or "",
            "snapshot": official.get("capacity_snapshot_period") or row.get("capacity_snapshot_period") or "",
            "detail": row.get("road_address") or row.get("lot_address") or "",
        }

    base_counts: dict[str, int] = {}
    for ambulance in scenario.ambulances.values():
        # ``home_base`` defaults to the start node, so placement mode is unchanged.
        home = ambulance.home_base or ambulance.location
        base_counts[home] = base_counts.get(home, 0) + 1
    for node, count in base_counts.items():
        if "::ambulance_base::" not in node:
            continue
        index = node.rsplit("::", 1)[-1]
        row = base_rows[int(index) - 1] if index.isdigit() and int(index) <= len(base_rows) else {}
        labels[node] = {
            "kind": "ambulance_base",
            "name": row.get("base_name") or node,
            "ambulance_count": count,
            "detail": row.get("matched_address") or row.get("address") or "",
        }

    for candidate in candidates:
        labels[candidate.location] = {
            "kind": "candidate",
            "name": candidate.name,
            "beds": candidate.capacity,
            "detail": candidate.source_id,
        }

    for post in posts:
        # An ``existing_base`` post shares its node with a station, and the
        # station label carries the fleet count, so it must not be overwritten.
        labels.setdefault(post.location, {
            "kind": "standby_post",
            "name": post.name,
            "detail": post.source_id,
        })

    for node in scenario.network.positions:
        labels.setdefault(node, {"kind": "other", "name": node, "detail": ""})
    if len(base_rows) < len(base_counts):
        raise ValueError(f"{code}: ambulance_bases.csv has fewer rows than the scenario's base nodes")
    return labels


def scenario_with_candidates(
    problem: MunicipalPlacementProblem, selected: tuple[CandidateHospital, ...]
) -> Scenario:
    """Clone the optimizer scenario and activate candidates (same as the optimizer)."""
    clone = scenario_from_dict(scenario_to_dict(problem.scenario), source=str(problem.scenario_path))
    for candidate in selected:
        clone.hospitals[candidate.id] = candidate.activate()
    return clone


@dataclass(frozen=True)
class ScenarioSpec:
    key: str
    label: str
    candidates: tuple[CandidateHospital, ...]
    is_best: bool


def resolve_scenarios(
    tokens: list[str], problem: MunicipalPlacementProblem, results: dict | None
) -> list[ScenarioSpec]:
    by_id = {candidate.id: candidate for candidate in problem.candidates}
    specs: list[ScenarioSpec] = []
    seen: dict[frozenset[str], str] = {}
    for token in tokens:
        raw = token.strip()
        if not raw:
            continue
        is_best = raw.lower() == "best"
        if raw.lower() == "baseline":
            selected: tuple[CandidateHospital, ...] = ()
        elif is_best:
            if results is None:
                raise ValueError("--scenarios best requires --results with an optimizer result file")
            ids = [str(value) for value in results.get("best", {}).get("candidate_ids", [])]
            if not ids:
                raise ValueError("the results file has no best.candidate_ids")
            missing = [ident for ident in ids if ident not in by_id]
            if missing:
                raise ValueError(f"results best.candidate_ids not in placement.json: {missing}")
            selected = tuple(by_id[ident] for ident in ids)
        else:
            ids = [part.strip() for part in raw.split("+") if part.strip()]
            missing = [ident for ident in ids if ident not in by_id]
            if missing:
                raise ValueError(f"unknown candidate id(s) {missing}; available: {sorted(by_id)}")
            selected = tuple(by_id[ident] for ident in ids)
        signature = frozenset(candidate.id for candidate in selected)
        if signature in seen:
            print(f"[알림] '{raw}' 는 이미 선택된 '{seen[signature]}' 와 같은 후보 조합이라 생략합니다.")
            continue
        if not selected:
            key, label = "baseline", "기준안: 기존 병원만"
        else:
            key = "+".join(candidate.id for candidate in selected)
            names = ", ".join(candidate.name for candidate in selected)
            prefix = "최적안" if is_best else "후보안"
            label = f"{prefix} {key} 추가: {names}"
        seen[signature] = raw
        specs.append(ScenarioSpec(key=key, label=label, candidates=selected, is_best=is_best))
    if not specs:
        raise ValueError("--scenarios selected nothing")
    return specs


@dataclass(frozen=True)
class StandbyPostView:
    """Only the manifest fields the playback needs to draw and describe a post."""

    id: str
    source_id: str
    name: str
    post_type: str
    location: str
    cell_population_estimate: float | None


@dataclass(frozen=True)
class StandbyInput:
    municipality_code: str
    municipality_name: str
    manifest_path: Path
    scenario_path: Path
    scenario: Scenario
    posts: tuple[StandbyPostView, ...]
    home_bases: dict[str, str]
    free_hours: tuple[int, ...]
    home_hours: tuple[int, ...]
    provenance: dict

    def post_locations(self) -> dict[str, str]:
        return {post.id: post.location for post in self.posts}


def standby_policy(free_hours: tuple[int, ...], home_hours: tuple[int, ...]):
    """Import the schedule policy late so a missing policy fails with a clear message."""
    try:
        from ambulance_sim.policy import ScheduledStandbyPolicy
    except ImportError as exc:  # pragma: no cover - only when the policy is absent
        raise ValueError(
            "ambulance_sim.policy.ScheduledStandbyPolicy 를 불러올 수 없습니다. "
            "대기지 재배치 모드는 이 정책이 있어야 재생할 수 있습니다."
        ) from exc
    return ScheduledStandbyPolicy(free_hours=free_hours, home_hours=home_hours)


def load_standby_input(path: Path) -> StandbyInput:
    """Read ``standby.json`` and its scenario.

    Only :mod:`ambulance_sim.io` is used, so the playback keeps working while the
    optimizer package changes; the full input contract is validated by
    ``ambulance_sim.standby_placement`` before the optimizer runs.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"{path}: root must be an object")

    def required(key: str):
        if key not in manifest:
            raise ValueError(f"{path}: missing required field {key!r}")
        return manifest[key]

    code = str(required("municipality_code")).strip()
    name = str(required("municipality_name")).strip()
    scenario_path = path.parent / str(required("scenario"))
    scenario = load_scenario(scenario_path)

    raw_posts = required("standby_posts")
    if not isinstance(raw_posts, list) or not raw_posts:
        raise ValueError(f"{path}: standby_posts must be a non-empty array")
    posts: list[StandbyPostView] = []
    for index, raw in enumerate(raw_posts):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: standby_posts[{index}] must be an object")
        for key in ("id", "source_id", "name", "post_type", "location"):
            if not str(raw.get(key, "")).strip():
                raise ValueError(f"{path}: standby_posts[{index}].{key} must be a non-empty string")
        location = str(raw["location"]).strip()
        if location not in scenario.network.positions:
            raise ValueError(f"{path}: standby post {raw['id']!r} node {location!r} has no coordinate in {scenario_path}")
        population = raw.get("cell_population_estimate")
        posts.append(StandbyPostView(
            id=str(raw["id"]).strip(),
            source_id=str(raw["source_id"]).strip(),
            name=str(raw["name"]).strip(),
            post_type=str(raw["post_type"]).strip(),
            location=location,
            cell_population_estimate=None if population is None else float(population),
        ))

    raw_homes = required("ambulance_home_bases")
    if not isinstance(raw_homes, dict) or not raw_homes:
        raise ValueError(f"{path}: ambulance_home_bases must be a non-empty object")
    home_bases = {str(ident).strip(): str(node).strip() for ident, node in raw_homes.items()}
    missing = sorted(set(scenario.ambulances) - set(home_bases))
    if missing:
        raise ValueError(f"{path}: ambulance_home_bases has no entry for {missing[0]!r}")
    for ident, node in home_bases.items():
        if node not in scenario.network.positions:
            raise ValueError(f"{path}: ambulance {ident} home base {node!r} has no coordinate in {scenario_path}")

    schedule = required("schedule")
    if not isinstance(schedule, dict):
        raise ValueError(f"{path}: schedule must be an object")

    def hours(key: str) -> tuple[int, ...]:
        values = schedule.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f"{path}: schedule.{key} must be a non-empty array of hours")
        parsed = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
                raise ValueError(f"{path}: schedule.{key} must contain integers from 0 to 23")
            parsed.append(value)
        return tuple(parsed)

    return StandbyInput(
        municipality_code=code,
        municipality_name=name,
        manifest_path=path,
        scenario_path=scenario_path,
        scenario=scenario,
        posts=tuple(posts),
        home_bases=home_bases,
        free_hours=hours("free_hours"),
        home_hours=hours("home_hours"),
        provenance=dict(manifest.get("provenance", {})),
    )


def scenario_with_assignment(standby: StandbyInput, assignment: dict[str, str]) -> Scenario:
    """Clone the optimizer scenario and point each ambulance at its post node."""
    clone = scenario_from_dict(scenario_to_dict(standby.scenario), source=str(standby.scenario_path))
    locations = standby.post_locations()
    unknown = sorted(set(assignment.values()) - set(locations))
    if unknown:
        raise ValueError(f"assignment refers to unknown standby post id(s): {unknown}")
    for ident, ambulance in clone.ambulances.items():
        post_id = assignment.get(ident)
        ambulance.assigned_post = None if post_id is None else locations[post_id]
    return clone


@dataclass
class StandbySpec:
    key: str
    label: str
    assignment: dict[str, str]
    is_best: bool


def moved_ambulances(standby: StandbyInput, assignment: dict[str, str]) -> list[str]:
    """Ambulances whose assigned post is not their own station."""
    locations = standby.post_locations()
    return [
        ident for ident, post_id in assignment.items()
        if post_id in locations and locations[post_id] != standby.home_bases.get(ident)
    ]


def resolve_standby_scenarios(
    tokens: list[str], standby: StandbyInput, results: dict | None
) -> list[StandbySpec]:
    specs: list[StandbySpec] = []
    seen: dict[frozenset, str] = {}
    for token in tokens:
        raw = token.strip()
        if not raw:
            continue
        lowered = raw.lower()
        if lowered == "baseline":
            assignment, key, label, is_best = {}, "baseline", "기준안: 전원 원소속 대기", False
        elif lowered == "best":
            if results is None:
                raise ValueError("--scenarios best requires --results with an optimizer result file")
            assignment = {
                str(ident): str(post) for ident, post in dict(results.get("best", {}).get("assignment", {})).items()
            }
            if not assignment:
                raise ValueError("the results file has no best.assignment")
            key, is_best = "best", True
            label = f"최적 배정: {len(moved_ambulances(standby, assignment))}대 이동"
        elif lowered.startswith("steps:"):
            if results is None:
                raise ValueError("--scenarios steps:N requires --results with an optimizer result file")
            number = lowered.split(":", 1)[1].strip()
            steps = list(results.get("steps", []))
            if not number.isdigit() or int(number) < 1:
                raise ValueError(f"'{raw}' is not a valid step selector; use steps:1, steps:2 ...")
            if int(number) > len(steps):
                raise ValueError(f"the results file has only {len(steps)} greedy step(s); '{raw}' is out of range")
            assignment = {
                str(ident): str(post) for ident, post in dict(steps[int(number) - 1].get("assignment", {})).items()
            }
            key, is_best = f"steps:{int(number)}", False
            label = f"탐욕 {int(number)}단계 배정: {len(moved_ambulances(standby, assignment))}대 이동"
        else:
            raise ValueError(f"unknown standby scenario {raw!r}; use baseline, best, or steps:N")
        signature = frozenset(assignment.items())
        if signature in seen:
            print(f"[알림] '{raw}' 는 이미 선택된 '{seen[signature]}' 와 같은 배정이라 생략합니다.")
            continue
        seen[signature] = raw
        specs.append(StandbySpec(key=key, label=label, assignment=assignment, is_best=is_best))
    if not specs:
        raise ValueError("--scenarios selected nothing")
    return specs


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def episode_payload(
    scenario: Scenario,
    *,
    policy,
    seed: int,
    node_index: dict[str, int],
    key: str,
    label: str,
    payload_extra: dict,
    digest_extra: dict,
) -> tuple[dict, dict]:
    """Run one episode and compress its trace into a delta-encoded payload."""
    simulation = Simulation(scenario, policy=policy, seed=seed, trace=True)
    summary = simulation.run()
    ambulance_ids = list(scenario.ambulances)
    ambulance_index = {ident: index for index, ident in enumerate(ambulance_ids)}
    hospital_ids = list(scenario.hospitals)
    hospital_index = {ident: index for index, ident in enumerate(hospital_ids)}
    profile_names = [profile.name for profile in scenario.profiles]
    profile_index = {name: index for index, name in enumerate(profile_names)}

    frames: list[list] = []
    log: list[list] = []
    previous_ambulances: dict[str, tuple] = {}
    previous_patients: dict[str, tuple] = {}
    onset: dict[str, float] = {}
    response_total, response_count = 0.0, 0

    for frame in simulation.trace:
        time = round(float(frame["time"]), 3)
        event = frame["event"]
        subject = frame["subject"]
        ambulance = ambulance_index.get(subject["ambulance_id"], -1)
        hospital = hospital_index.get(subject["hospital_id"], -1) if subject["hospital_id"] else -1
        patient, node = -1, -1
        raw_patient = subject["patient_id"]
        if raw_patient:
            # The engine reuses ``patient_id`` for village and standby nodes.
            if raw_patient in node_index:
                node = node_index[raw_patient]
            else:
                patient = int(raw_patient[1:])
        if event == "patient_arrival":
            patient = len(frame["patients"])
            onset[f"P{patient:04d}"] = time
        if event == "arrive_scene" and raw_patient in onset:
            response_total += max(0.0, time - onset[raw_patient])
            response_count += 1

        log_kind = LOG_FROM_EVENT.get(event)
        if log_kind is not None:
            log.append([time, log_kind, ambulance, patient, node, hospital, len(frames)])

        ambulance_delta: list = []
        for index, ident in enumerate(ambulance_ids):
            record = frame["ambulances"][ident]
            destination = record["destination"]
            value = (
                node_index[record["location"]],
                AMBULANCE_STATUS.index(record["status"]),
                node_index[destination] if destination else -1,
                _round(record["movement_started_at"], 3),
                _round(record["movement_ends_at"], 3),
            )
            previous = previous_ambulances.get(ident)
            if previous == value:
                continue
            ambulance_delta.extend([index, *value])
            previous_ambulances[ident] = value
            # A new leg is either a status change or a new destination: a transfer
            # keeps the ambulance in ``to_hospital`` while re-routing it.
            new_leg = previous is None or previous[1] != value[1] or previous[2] != value[2]
            if new_leg:
                carried = record["patient_id"]
                carried_number = int(carried[1:]) if carried and carried.startswith("P") else -1
                if record["status"] == "to_scene":
                    log.append([time, 1, index, carried_number, value[2], -1, len(frames)])
                elif record["status"] == "to_hospital":
                    log.append([time, 4, index, carried_number, value[2], -1, len(frames)])

        patient_delta: list = []
        for ident, record in frame["patients"].items():
            value = (
                node_index[record["location"]],
                PATIENT_STATUS.index(record["status"]),
                profile_index[record["profile"]],
            )
            if previous_patients.get(ident) == value:
                continue
            patient_delta.extend([int(ident[1:]), *value])
            previous_patients[ident] = value

        totals = frame["totals"]
        frames.append([
            time,
            FRAME_EVENTS.index(event),
            ambulance,
            patient,
            hospital,
            node,
            round(float(totals["saved"]), 3),
            round(float(totals["lost"]), 3),
            int(totals["waiting"]),
            len(frame["patients"]),
            round(response_total / response_count, 2) if response_count else -1,
            ambulance_delta,
            patient_delta,
        ])

    payload = {
        "key": key,
        "label": label,
        "seed": seed,
        **payload_extra,
        "ambulances": ambulance_ids,
        "hospitals": [
            {
                "id": ident,
                "node": node_index[scenario.hospitals[ident].location],
                "capacity": scenario.hospitals[ident].capacity,
            }
            for ident in hospital_ids
        ],
        "profiles": profile_names,
        "horizon_minutes": scenario.horizon_minutes,
        "frames": frames,
        "log": log,
    }
    digest = {
        "key": key,
        "label": label,
        "seed": seed,
        **digest_extra,
        "patients": summary["seeded_patients"],
        "expected_saved": summary["expected_saved"],
        "expected_lost": summary["expected_lost"],
        "expected_saved_per_patient": summary["expected_saved_per_patient"],
        "response": {
            "mean_minutes": summary["mean_response_minutes"],
            "p90_minutes": summary["p90_response_minutes"],
            "reached_patients": summary["reached_patients"],
            "unreached_patients": summary["unreached_patients"],
            "response_rate": summary["response_rate"],
        },
        "events": summary["events"],
        "frame_count": len(frames),
        "log_entry_count": len(log),
    }
    return payload, digest


def build_scenario_payload(
    problem: MunicipalPlacementProblem,
    spec: ScenarioSpec,
    *,
    seed: int,
    node_index: dict[str, int],
) -> tuple[dict, dict]:
    return episode_payload(
        scenario_with_candidates(problem, spec.candidates),
        policy=GreedySurvivalPolicy(),
        seed=seed,
        node_index=node_index,
        key=spec.key,
        label=spec.label,
        payload_extra={
            "is_best": spec.is_best,
            "activated": [candidate.id for candidate in spec.candidates],
            "activated_names": [candidate.name for candidate in spec.candidates],
        },
        digest_extra={
            "is_best": spec.is_best,
            "activated_candidate_ids": [candidate.id for candidate in spec.candidates],
            "activated_candidate_names": [candidate.name for candidate in spec.candidates],
        },
    )


def build_standby_scenario_payload(
    standby: StandbyInput,
    spec: StandbySpec,
    *,
    seed: int,
    node_index: dict[str, int],
    post_index: dict[str, int],
) -> tuple[dict, dict]:
    scenario = scenario_with_assignment(standby, spec.assignment)
    ambulance_order = {ident: index for index, ident in enumerate(scenario.ambulances)}
    pairs = [
        [ambulance_order[ident], post_index[post_id]]
        for ident, post_id in spec.assignment.items()
        if ident in ambulance_order
    ]
    pairs.sort()
    moved = moved_ambulances(standby, spec.assignment)
    return episode_payload(
        scenario,
        policy=standby_policy(standby.free_hours, standby.home_hours),
        seed=seed,
        node_index=node_index,
        key=spec.key,
        label=spec.label,
        payload_extra={"is_best": spec.is_best, "assignment": pairs},
        digest_extra={
            "is_best": spec.is_best,
            "assignment": dict(spec.assignment),
            "moved_ambulances": moved,
        },
    )


def add_grid(map_object: folium.Map, coordinates: list[tuple[float, float]], size_km: float) -> int:
    grid = folium.FeatureGroup(name=f"{size_km:g} km 분석격자 (행정경계 아님)", show=True)
    center_lat = sum(lat for lat, _ in coordinates) / len(coordinates)
    lat_step = size_km / 111.32
    lon_step = size_km / (111.32 * math.cos(math.radians(center_lat)))
    min_lat, max_lat = min(x[0] for x in coordinates) - lat_step, max(x[0] for x in coordinates) + lat_step
    min_lon, max_lon = min(x[1] for x in coordinates) - lon_step, max(x[1] for x in coordinates) + lon_step
    rows = math.ceil((max_lat - min_lat) / lat_step)
    columns = math.ceil((max_lon - min_lon) / lon_step)
    for row in range(rows):
        for column in range(columns):
            south, west = min_lat + row * lat_step, min_lon + column * lon_step
            folium.Rectangle(
                bounds=[[south, west], [south + lat_step, west + lon_step]],
                color="#64748b", weight=0.65, opacity=0.55, fill=False,
                tooltip=f"GRID-{row:03d}-{column:03d} · {size_km:g}km 분석",
            ).add_to(grid)
    grid.add_to(map_object)
    return rows * columns


def _estimate_cell(estimate: dict | None) -> str:
    if not isinstance(estimate, dict) or estimate.get("mean") is None:
        return "—"
    mean = f"{float(estimate['mean']):.2f}"
    low, high = estimate.get("ci95_low"), estimate.get("ci95_high")
    if low is None or high is None:
        return f"{mean} (CI 불가)"
    return f"{mean} [{float(low):.2f}, {float(high):.2f}]"


def _parameter_line(model_parameters: dict) -> str:
    status = str(model_parameters.get("status", "")).strip() or "미기재"
    return (
        f"모델 계수 상태: <b>{escape(status)}</b> · 경로 {escape(str(model_parameters.get('path', '미기재')))}"
        f" · 생성 {escape(str(model_parameters.get('generated_at', '미기재')))}"
    )


def results_panel_html(results: dict | None, results_path: Path | None, model_parameters: dict) -> str:
    parameter_line = _parameter_line(model_parameters)
    if results is None:
        return (
            '<div id="sim-results"><div class="sim-h">최적화 결과</div>'
            '<div class="sim-empty">--results 가 지정되지 않아 결과표를 생략했습니다. '
            '재생 화면은 시나리오별 단일 에피소드입니다.</div>'
            f'<div class="sim-src">{parameter_line}</div></div>'
        )
    best_ids = [str(value) for value in results.get("best", {}).get("candidate_ids", [])]
    rows = []
    for rank, alternative in enumerate(results.get("alternatives", []), start=1):
        ids = [str(value) for value in alternative.get("candidate_ids", [])]
        names = ", ".join(str(name) for name in alternative.get("candidate_names", [])) or "—"
        highlight = ' class="sim-best"' if ids == best_ids else ""
        rows.append(
            f"<tr{highlight}><td>{rank}</td><td>{escape('+'.join(ids))}<br><span class='sim-dim'>{escape(names)}</span></td>"
            f"<td>{_estimate_cell(alternative.get('expected_saved'))}</td>"
            f"<td>{_estimate_cell(alternative.get('incremental_expected_saved'))}</td></tr>"
        )
    baseline = _estimate_cell(results.get("baseline_existing_only"))
    source = escape(str(results_path)) if results_path else "미기재"
    return (
        '<div id="sim-results"><div class="sim-h">최적화 결과 '
        f"<span class='sim-dim'>{escape(str(results.get('municipality_name', '')))} · 신설 "
        f"{escape(str(results.get('new_hospitals', '?')))}개 · {escape(str(results.get('episodes', '?')))} 에피소드</span></div>"
        f'<div class="sim-base">기준안(기존 병원만) 기대 생존: <b>{baseline}</b> <span class="sim-dim">mean [95% CI]</span></div>'
        '<table class="sim-table"><thead><tr><th>순위</th><th>후보</th><th>기대 생존</th><th>증분</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
        f'<div class="sim-src">결과 파일: {source}<br>{parameter_line}</div></div>'
    )


def schedule_rule_html(standby: StandbyInput) -> str:
    """The operating rule, plus a warning when the manifest disagrees with it."""
    note = ""
    if frozenset(standby.home_hours) != RULE_HOME_HOURS:
        note = (
            '<br><span class="sim-warn-inline">※ 입력 schedule 이 위 문장과 다릅니다 — 복귀 시간 '
            f"{escape(', '.join(f'{hour:02d}' for hour in sorted(set(standby.home_hours))))}시</span>"
        )
    return f'<div class="sim-rule">운영 규칙: {escape(SCHEDULE_RULE)}{note}</div>'


def _population_text(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}명"


def standby_results_panel_html(
    results: dict | None,
    results_path: Path | None,
    model_parameters: dict,
    standby: StandbyInput,
    ambulance_order: list[str],
) -> str:
    parameter_line = _parameter_line(model_parameters)
    rule = schedule_rule_html(standby)
    if results is None:
        return (
            '<div id="sim-results"><div class="sim-h">대기지 재배치 결과</div>' + rule
            + '<div class="sim-empty">--results 가 지정되지 않아 결과표를 생략했습니다. '
            '재생 화면은 시나리오별 단일 에피소드입니다.</div>'
            f'<div class="sim-src">{parameter_line}</div></div>'
        )
    posts = {post.id: post for post in standby.posts}
    colour = {ident: AMBULANCE_PALETTE[index % len(AMBULANCE_PALETTE)] for index, ident in enumerate(ambulance_order)}
    best = dict(results.get("best", {}))
    assignment = {str(ident): str(post) for ident, post in dict(best.get("assignment", {})).items()}
    moved = [str(ident) for ident in best.get("moved_ambulances", [])] or moved_ambulances(standby, assignment)

    moved_rows = []
    for ident in moved:
        post = posts.get(assignment.get(ident, ""))
        swatch = f'<span class="sim-dot" style="background:{colour.get(ident, "#94a3b8")}"></span>'
        moved_rows.append(
            f"<tr><td>{swatch}{escape(ident)}</td><td>{escape(assignment.get(ident, '—'))}</td>"
            f"<td>{escape(post.name) if post else '—'}<br><span class='sim-dim'>"
            f"{escape(POST_TYPE_LABELS.get(post.post_type, post.post_type)) if post else '—'}</span></td>"
            f"<td>{_population_text(post.cell_population_estimate) if post else '—'}</td></tr>"
        )
    if not moved_rows:
        moved_rows.append('<tr><td colspan="4" class="sim-dim">이동한 구급차가 없습니다.</td></tr>')

    step_rows = []
    for step in results.get("steps", []):
        post = posts.get(str(step.get("post_id", "")))
        step_rows.append(
            f"<tr><td>{escape(str(step.get('step', '?')))}</td><td>{escape(str(step.get('ambulance_id', '—')))}</td>"
            f"<td>{escape(str(step.get('post_id', '—')))}<br><span class='sim-dim'>"
            f"{escape(str(step.get('post_name') or (post.name if post else '—')))}</span></td>"
            f"<td>{_estimate_cell(step.get('incremental_expected_saved'))}</td></tr>"
        )
    if not step_rows:
        step_rows.append('<tr><td colspan="4" class="sim-dim">탐욕 단계 기록이 없습니다.</td></tr>')

    source = escape(str(results_path)) if results_path else "미기재"
    return (
        '<div id="sim-results"><div class="sim-h">대기지 재배치 결과 '
        f"<span class='sim-dim'>{escape(str(results.get('municipality_name', '')))} · "
        f"{escape(str(results.get('episodes', '?')))} 에피소드 · 탐욕 해</span></div>" + rule
        + f'<div class="sim-base">기준안(전원 원소속) 기대 생존: <b>{_estimate_cell(results.get("baseline_home_bases"))}</b>'
        ' <span class="sim-dim">mean [95% CI]</span></div>'
        f'<div class="sim-base">최적 배정 기대 생존: <b>{_estimate_cell(best.get("expected_saved"))}</b></div>'
        f'<div class="sim-base">증분(최적 − 기준): <b>{_estimate_cell(best.get("incremental_expected_saved"))}</b></div>'
        f'<div class="sim-h">이동 구급차 {len(moved)}대</div>'
        '<table class="sim-table"><thead><tr><th>구급차</th><th>대기지</th><th>대기지 명</th><th>칸 인구 추정</th></tr></thead>'
        f"<tbody>{''.join(moved_rows)}</tbody></table>"
        '<div class="sim-h">탐욕 배정 단계</div>'
        '<table class="sim-table"><thead><tr><th>단계</th><th>구급차</th><th>대기지</th><th>증분 평균</th></tr></thead>'
        f"<tbody>{''.join(step_rows)}</tbody></table>"
        f'<div class="sim-src">결과 파일: {source}<br>{parameter_line}</div></div>'
    )


def build_map(
    *,
    mode: str,
    villages: dict[str, float],
    labels: dict[str, dict],
    coordinates: dict[str, tuple[float, float]],
    node_order: list[str],
    scenarios: list[dict],
    results_html: str,
    extras: dict,
    metadata: dict,
    output: Path,
    grid_km: float,
) -> int:
    demand_coordinates = [coordinates[node] for node in villages]
    center = [
        sum(x[0] for x in demand_coordinates) / len(demand_coordinates),
        sum(x[1] for x in demand_coordinates) / len(demand_coordinates),
    ]
    map_object = folium.Map(location=center, zoom_start=12, tiles="CartoDB positron", control_scale=True)
    Fullscreen(position="topleft").add_to(map_object)
    MeasureControl(position="topleft", primary_length_unit="kilometers").add_to(map_object)
    grid_count = add_grid(map_object, demand_coordinates, grid_km)

    heat = [[*coordinates[node], labels[node]["population"]] for node in villages]
    HeatMap(heat, name="주민등록인구 가중 수요분포", radius=24, blur=18, show=True).add_to(map_object)

    demand_layer = folium.FeatureGroup(name=f"수요 대표점 {len(villages)}개", show=True)
    for node, hourly_rate in villages.items():
        label = labels[node]
        folium.CircleMarker(
            location=coordinates[node], radius=4, color="#7c3aed", fill=True, fill_opacity=0.8,
            tooltip=f"{label['name']} · 인구 {label['population']:,}명 · 모델 시간당 {hourly_rate:.3f}건",
            popup="행정복지센터 대표좌표이며 실제 신고 위치가 아닙니다.",
        ).add_to(demand_layer)
    demand_layer.add_to(map_object)

    base_nodes = [node for node in node_order if labels[node]["kind"] == "ambulance_base"]
    base_name = "119안전센터(원소속)" if mode == "standby" else "119 구급차 거점"
    base_layer = folium.FeatureGroup(name=f"{base_name} {len(base_nodes)}곳", show=True)
    for node in base_nodes:
        label = labels[node]
        folium.Marker(
            coordinates[node],
            tooltip=f"{label['name']} · 구급차 {label['ambulance_count']}대",
            popup=label["detail"] or label["name"],
            icon=folium.Icon(color="blue", icon="plus", prefix="fa"),
        ).add_to(base_layer)
    base_layer.add_to(map_object)

    hospital_nodes = [node for node in node_order if labels[node]["kind"] == "hospital"]
    hospital_layer = folium.FeatureGroup(name=f"기존 응급의료기관 {len(hospital_nodes)}곳", show=True)
    for node in hospital_nodes:
        label = labels[node]
        folium.Marker(
            coordinates[node],
            tooltip=f"{label['name']} · {label['category']} · 응급실 {label['beds']}병상",
            popup=(
                f"{label['detail']}<br>HIRA {label['snapshot']} 응급실 {label['beds']}병상"
                f" · 중환자 {label['critical_care_beds']}병상 (실시간 가용병상이 아님)"
            ),
            icon=folium.Icon(color="red", icon="hospital", prefix="fa"),
        ).add_to(hospital_layer)
    hospital_layer.add_to(map_object)

    if mode == "standby":
        layer_a = folium.FeatureGroup(name=f"격자 대기지 후보 {len(extras.get('posts', []))}곳", show=True)
        layer_b = folium.FeatureGroup(name="배정 대기지 · 원소속→대기지 이동선", show=True)
    else:
        layer_a = folium.FeatureGroup(name="신설 후보(가동)", show=True)
        layer_b = folium.FeatureGroup(name="계획검토 후보(미가동)", show=True)
    layer_a.add_to(map_object)
    layer_b.add_to(map_object)
    folium.LayerControl(collapsed=True).add_to(map_object)

    payload = {
        "mode": mode,
        "nodes": node_order,
        "coords": [list(coordinates[node]) for node in node_order],
        "names": [labels[node]["name"] for node in node_order],
        "kinds": [labels[node]["kind"] for node in node_order],
        **extras,
        "ambulance_status": AMBULANCE_STATUS,
        "patient_status": PATIENT_STATUS,
        "frame_events": FRAME_EVENTS,
        "log_kinds": LOG_KINDS,
        "scenarios": scenarios,
        "metadata": metadata | {"grid_cell_count": grid_count, "grid_km": grid_km},
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    status = str(metadata.get("model_parameters", {}).get("status", "")).strip()
    if status == "provisional":
        banner = f'<div id="sim-warn">{escape(PROVISIONAL_WARNING)}</div>'
    elif status != "calibrated":
        banner = '<div id="sim-warn">모델 계수 상태 미기재 — 절대값을 인용하지 말 것</div>'
    else:
        banner = ""

    map_object.get_root().header.add_child(Element(STYLE))
    map_object.get_root().html.add_child(Element(
        banner + SIDE_LOG + results_html + controls_html(mode)
    ))
    map_object.get_root().script.add_child(Element(
        SCRIPT.replace("__MAP__", map_object.get_name())
        .replace("__LAYER_A__", layer_a.get_name())
        .replace("__LAYER_B__", layer_b.get_name())
        .replace("__DATA__", data)
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    map_object.save(str(output))
    return grid_count


def load_results(requested: Path | None, municipality_code: str) -> tuple[dict | None, Path | None]:
    """Read an optimizer result file, tolerating one that has not been produced yet."""
    if requested is None:
        return None, None
    path = requested.resolve()
    if not path.exists():
        print(f"[알림] 결과 파일이 아직 없어 결과표를 생략합니다: {path}")
        return None, None
    results = json.loads(path.read_text(encoding="utf-8"))
    if str(results.get("municipality_code")) != municipality_code:
        raise ValueError(
            f"{path}: results municipality {results.get('municipality_code')!r} "
            f"does not match the input manifest {municipality_code!r}"
        )
    return results, path


def write_summary(output: Path, metadata: dict, *, grid_km: float, grid_count: int,
                  digests: list[dict], provenance: dict) -> Path:
    summary_path = output.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(
            metadata | {
                "grid_km": grid_km,
                "grid_cell_count": grid_count,
                "scenarios": digests,
                "provenance": provenance,
            },
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    size = output.stat().st_size
    print(f"{output}  ({size:,} bytes / {size / 1_048_576:.2f} MiB)")
    print(summary_path)
    return summary_path


def run_placement(args: argparse.Namespace) -> int:
    placement_path = args.placement.resolve()
    problem = load_municipal_placement_problem(placement_path)
    manifest = json.loads(placement_path.read_text(encoding="utf-8"))
    raw_scenario = json.loads(problem.scenario_path.read_text(encoding="utf-8"))

    requested_results = str(args.results.resolve()) if args.results else None
    results, results_path = load_results(args.results, problem.municipality_code)

    tokens = args.scenarios.split(",")
    if results is None and [token.strip().lower() for token in tokens] == ["baseline", "best"]:
        print("[알림] 결과 파일이 없어 'best' 시나리오를 생략하고 기준안만 재생합니다.")
        tokens = ["baseline"]
    specs = resolve_scenarios(tokens, problem, results)

    convert = coordinate_converter(problem.provenance)
    coordinates = {
        node: convert(node, position) for node, position in problem.scenario.network.positions.items()
    }
    node_order = list(problem.scenario.network.positions)
    node_index = {node: index for index, node in enumerate(node_order)}
    folder = municipality_folder(args.processed_dir.resolve(), problem.municipality_code)
    labels = node_labels(
        problem.scenario, problem.municipality_code, folder, raw_scenario, candidates=problem.candidates
    )

    scenario_payloads, digests = [], []
    for spec in specs:
        payload, digest = build_scenario_payload(problem, spec, seed=args.seed, node_index=node_index)
        scenario_payloads.append(payload)
        digests.append(digest)
        print(
            f"[시나리오] {spec.key}: 환자 {digest['patients']}명 · 기대 생존 {digest['expected_saved']:.3f}"
            f" · 기대 손실 {digest['expected_lost']:.3f} · 프레임 {digest['frame_count']} · 로그 {digest['log_entry_count']}"
        )

    metadata = {
        "generated_by": "scripts/build_folium_municipal_simulator.py",
        "municipality_code": problem.municipality_code,
        "municipality_name": problem.municipality_name,
        "placement_manifest": str(placement_path),
        "scenario_file": str(problem.scenario_path),
        "processed_csv_folder": str(folder),
        "results_file": str(results_path) if results_path else None,
        "results_file_requested": requested_results,
        "policy": POLICY_LABEL,
        "seed": args.seed,
        "coordinate_order": problem.provenance.get("coordinate_order"),
        "model_parameters": dict(problem.provenance.get("model_parameters", {})),
        "caveats": [
            "행정동 점은 행정복지센터 대표좌표이며 실제 신고 좌표가 아닙니다.",
            "지도 위 구급차 이동선은 재생용 직선 보간이며, 소요시간만 카카오 자동차 경로 실측값입니다.",
            "병상 수는 HIRA 정적 신고값이며 실시간 가용병상이 아닙니다.",
            "1km 격자는 분석용 사각격자이며 행정경계가 아닙니다.",
            "재생 화면은 시나리오별 단일 에피소드(고정 시드)이며, 결과표의 평균/CI는 다중 에피소드 결과입니다.",
        ],
    }
    raw_candidates = {
        str(record.get("id")): record for record in manifest.get("candidate_hospitals", [])
    }
    extras = {
        "candidates": [
            {
                "id": candidate.id,
                "name": candidate.name,
                "node": node_index[candidate.location],
                "capacity": candidate.capacity,
                "land_feasibility_status": str(
                    raw_candidates.get(candidate.id, {}).get("land_feasibility_status", "미기재")
                ),
                "category_assumption": str(
                    raw_candidates.get(candidate.id, {}).get("category_assumption", "미기재")
                ),
            }
            for candidate in problem.candidates
        ]
    }
    output = args.output.resolve()
    grid_count = build_map(
        mode="placement", villages=problem.scenario.villages, labels=labels, coordinates=coordinates,
        node_order=node_order, scenarios=scenario_payloads,
        results_html=results_panel_html(results, results_path, metadata["model_parameters"]),
        extras=extras, metadata=metadata, output=output, grid_km=args.grid_km,
    )
    write_summary(
        output, metadata, grid_km=args.grid_km, grid_count=grid_count,
        digests=digests, provenance=manifest.get("provenance", {}),
    )
    return 0


def run_standby(args: argparse.Namespace) -> int:
    standby_path = args.standby.resolve()
    standby = load_standby_input(standby_path)
    manifest = json.loads(standby_path.read_text(encoding="utf-8"))
    raw_scenario = json.loads(standby.scenario_path.read_text(encoding="utf-8"))

    requested_results = str(args.results.resolve()) if args.results else None
    results, results_path = load_results(args.results, standby.municipality_code)

    tokens = args.scenarios.split(",")
    if results is None and [token.strip().lower() for token in tokens] == ["baseline", "best"]:
        print("[알림] 결과 파일이 없어 'best' 시나리오를 생략하고 기준안만 재생합니다.")
        tokens = ["baseline"]
    specs = resolve_standby_scenarios(tokens, standby, results)

    convert = coordinate_converter(standby.provenance)
    coordinates = {
        node: convert(node, position) for node, position in standby.scenario.network.positions.items()
    }
    node_order = list(standby.scenario.network.positions)
    node_index = {node: index for index, node in enumerate(node_order)}
    post_index = {post.id: index for index, post in enumerate(standby.posts)}
    folder = municipality_folder(args.processed_dir.resolve(), standby.municipality_code)
    labels = node_labels(
        standby.scenario, standby.municipality_code, folder, raw_scenario, posts=standby.posts
    )

    scenario_payloads, digests = [], []
    for spec in specs:
        payload, digest = build_standby_scenario_payload(
            standby, spec, seed=args.seed, node_index=node_index, post_index=post_index
        )
        scenario_payloads.append(payload)
        digests.append(digest)
        print(
            f"[시나리오] {spec.key}: 이동 {len(digest['moved_ambulances'])}대 · 환자 {digest['patients']}명"
            f" · 기대 생존 {digest['expected_saved']:.3f} · 기대 손실 {digest['expected_lost']:.3f}"
            f" · 프레임 {digest['frame_count']} · 로그 {digest['log_entry_count']}"
        )

    metadata = {
        "generated_by": "scripts/build_folium_municipal_simulator.py",
        "mode": "standby",
        "municipality_code": standby.municipality_code,
        "municipality_name": standby.municipality_name,
        "standby_manifest": str(standby_path),
        "scenario_file": str(standby.scenario_path),
        "processed_csv_folder": str(folder),
        "results_file": str(results_path) if results_path else None,
        "results_file_requested": requested_results,
        "policy": STANDBY_POLICY_LABEL,
        "schedule": {"free_hours": list(standby.free_hours), "home_hours": list(standby.home_hours)},
        "schedule_rule": SCHEDULE_RULE,
        "seed": args.seed,
        "coordinate_order": standby.provenance.get("coordinate_order"),
        "model_parameters": dict(standby.provenance.get("model_parameters", {})),
        "caveats": [
            "행정동 점은 행정복지센터 대표좌표이며 실제 신고 좌표가 아닙니다.",
            "지도 위 구급차 이동선은 재생용 직선 보간이며, 소요시간만 카카오 자동차 경로 실측값입니다.",
            "대기지는 1km 격자 칸의 중심점이며, 실제로 구급차가 주차·대기할 수 있는지는 검증되지 않았습니다.",
            "1km 격자는 분석용 사각격자이며 행정경계가 아닙니다.",
            "배정은 탐욕 순차 해이며 전역 최적이 아닙니다.",
            "재생 화면은 시나리오별 단일 에피소드(고정 시드)이며, 결과표의 평균/CI는 다중 에피소드 결과입니다.",
        ],
    }
    extras = {
        "posts": [
            {
                "id": post.id,
                "name": post.name,
                "node": node_index[post.location],
                "post_type": post.post_type,
                "type_label": POST_TYPE_LABELS.get(post.post_type, post.post_type),
                "population": post.cell_population_estimate,
                "source_id": post.source_id,
            }
            for post in standby.posts
        ],
        "homes": {
            ident: node_index[node] for ident, node in standby.home_bases.items() if node in node_index
        },
        "palette": list(AMBULANCE_PALETTE),
        "schedule_rule": SCHEDULE_RULE,
    }
    output = args.output.resolve()
    grid_count = build_map(
        mode="standby", villages=standby.scenario.villages, labels=labels, coordinates=coordinates,
        node_order=node_order, scenarios=scenario_payloads,
        results_html=standby_results_panel_html(
            results, results_path, metadata["model_parameters"], standby, list(standby.scenario.ambulances)
        ),
        extras=extras, metadata=metadata, output=output, grid_km=args.grid_km,
    )
    write_summary(
        output, metadata, grid_km=args.grid_km, grid_count=grid_count,
        digests=digests, provenance=manifest.get("provenance", {}),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--placement", type=Path, help="optimizer placement.json manifest (hospital mode)")
    source.add_argument("--standby", type=Path, help="optimizer standby.json manifest (repositioning mode)")
    parser.add_argument("--processed-dir", type=Path, required=True, help="processed municipality CSV root (map labels only)")
    parser.add_argument("--results", type=Path, help="optimizer result JSON to render as a results table")
    parser.add_argument(
        "--scenarios", default="baseline,best",
        help="hospital mode: baseline,best,C01,C01+C03 ... / standby mode: baseline,best,steps:N",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--grid-km", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    if args.grid_km <= 0:
        parser.error("--grid-km must be positive")
    return run_standby(args) if args.standby is not None else run_placement(args)


STYLE = r"""<style>
/* Folium bundles Bootstrap, whose `.row > *` rule forces full-width children,
   so every simulator class is prefixed instead of reusing generic names. */
#sim-panel,#sim-side,#sim-warn,#sim-results{position:fixed;z-index:9999;font-family:"Pretendard","Malgun Gothic",sans-serif;color:#f8fafc}
#sim-panel,#sim-warn,#sim-results,#sim-log-box{background:#0b1f2aee;border:1px solid #3b5968;box-shadow:0 10px 28px #0005}
#sim-panel{left:352px;right:12px;bottom:12px;border-radius:12px;padding:11px 15px}
#sim-panel .sim-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
#sim-panel button,#sim-panel select{background:#153747;color:#fff;border:1px solid #527181;border-radius:6px;padding:6px 10px;font:inherit;width:auto;flex:0 0 auto}
#sim-panel button{background:#ef5b35;font-weight:700;cursor:pointer}
#sim-time{font-size:21px;font-weight:700;font-variant-numeric:tabular-nums}
#sim-range{flex:1 1 200px;min-width:180px;accent-color:#ef5b35}
#sim-scenario{max-width:320px}
.sim-stat{font-size:12px;color:#c5d5dd;white-space:nowrap}.sim-stat b{color:#fff;font-size:13px}
.sim-note{margin-top:7px;font-size:11px;color:#facc15;line-height:1.5}
#sim-warn{top:12px;left:352px;right:72px;border-radius:10px;padding:8px 14px;font-weight:700;font-size:13px;background:#7f1d1dee;border-color:#fca5a5}
#sim-side{left:12px;top:70px;bottom:12px;width:328px;display:flex;flex-direction:column}
#sim-log-box{flex:1;min-height:0;display:flex;flex-direction:column;border-radius:10px;padding:9px 10px;font-size:11px}
#sim-log{flex:1;min-height:0;overflow-y:auto}
#sim-results{right:12px;top:70px;width:min(430px,36vw);max-height:52vh;overflow:auto;border-radius:10px;padding:9px 11px;font-size:11px}
.sim-h{font-size:13px;font-weight:700;margin-bottom:6px}
.sim-dim{color:#9fb3bd;font-weight:400}
.sim-base{margin-bottom:6px;color:#e2e8f0}
.sim-rule{margin-bottom:7px;padding:4px 6px;border-left:3px solid #ef5b35;background:#12303deb;color:#fde68a;line-height:1.5}
.sim-warn-inline{color:#fca5a5}
.sim-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle;border:1px solid #0b1f2a}
.sim-empty{color:#cbd5e1;line-height:1.5}
.sim-table{border-collapse:collapse;width:100%}
.sim-table th,.sim-table td{border-bottom:1px solid #2c4b5a;padding:3px 4px;text-align:left;vertical-align:top;color:#f1f5f9}
.sim-table th{color:#9fb3bd;font-weight:700}
.sim-table tr.sim-best{background:#14532d99}
.sim-table tr.sim-best td{color:#eafce8}
.sim-src{margin-top:6px;color:#9fb3bd;line-height:1.5;word-break:break-all}
.sim-log-row{padding:3px 4px;border-bottom:1px solid #21414f;cursor:pointer;line-height:1.45}
.sim-log-row:hover{background:#1b3f4f}
.sim-log-row .lt{color:#7dd3fc;font-variant-numeric:tabular-nums;margin-right:5px}
.sim-log-row .lk{font-weight:700;margin-right:5px}
.sim-log-row .ld{color:#cbd5e1}
.ambulance-pin{font-size:22px;filter:drop-shadow(0 1px 2px #000);text-align:center}
.ambulance-pin.st-to_scene{filter:drop-shadow(0 0 5px #f87171)}
.ambulance-pin.st-to_hospital{filter:drop-shadow(0 0 5px #38bdf8)}
.ambulance-pin.st-on_scene{filter:drop-shadow(0 0 5px #facc15)}
.cand-pin{width:26px;height:26px;line-height:23px;text-align:center;border-radius:50%;font-size:15px;font-weight:700;color:#fff;border:2px solid #fff;box-shadow:0 1px 4px #0007}
.cand-pin.cand-on{background:#16a34a}
.cand-pin.cand-off{background:#f59e0b}
</style>"""

SIDE_LOG = r"""<div id="sim-side"><div id="sim-log-box"><div class="sim-h">이벤트 로그 <span class="sim-dim">행을 클릭하면 해당 시각으로 이동</span></div><div id="sim-log"></div></div></div>"""

CONTROLS = r"""<div id="sim-panel"><div class="sim-row"><select id="sim-scenario"></select><button id="sim-play">재생</button><span id="sim-time">00:00</span><input id="sim-range" type="range" min="0" max="1440" value="0" step="1"><select id="sim-speed"><option value="30">30분/초</option><option value="120" selected>2시간/초</option><option value="600">10시간/초</option></select></div>
<div class="sim-row" style="margin-top:7px"><span class="sim-stat">경과 시각 <b id="sim-elapsed">0분</b></span><span class="sim-stat">발생 환자 <b id="sim-patients">0</b>명</span><span class="sim-stat">기대 생존 <b id="sim-saved">0.00</b></span><span class="sim-stat">기대 손실 <b id="sim-lost">0.00</b></span><span class="sim-stat">평균 응답시간 <b id="sim-resp">—</b></span><span class="sim-stat">출동 대기 <b id="sim-waiting">0</b>명</span></div>
<div class="sim-note">__NOTE__</div></div>"""

COMMON_NOTE = (
    "1km 격자는 분석용 사각격자이며 행정경계가 아닙니다. 행정동 점은 실제 신고점이 아닌 행정복지센터 대표좌표입니다. "
    "이동시간은 카카오 자동차 경로 실측 요약값이지만, 지도 위 이동선은 재생용 직선 보간입니다. 재생 화면은 고정 시드 1개 에피소드입니다."
)
STANDBY_NOTE = (
    f"운영 규칙: {SCHEDULE_RULE}. 대기지는 격자 칸 중심점이며 실제 주차·대기 가능 여부는 검증되지 않았습니다. " + COMMON_NOTE
)


def controls_html(mode: str) -> str:
    return CONTROLS.replace("__NOTE__", escape(STANDBY_NOTE if mode == "standby" else COMMON_NOTE))

SCRIPT = r'''
// Folium appends the map's own script after this element, so the map and its
// feature-group variables only exist once the document has finished parsing.
document.addEventListener('DOMContentLoaded',function(){
const D=__DATA__,SIM_MAP=__MAP__,LAYER_A=__LAYER_A__,LAYER_B=__LAYER_B__;
const LOG_MAX=400;
const el=id=>document.getElementById(id);
const logWrap=el('sim-log');
const units={},patientLayer=L.layerGroup().addTo(SIM_MAP);
let scenarioIndex=0,simT=0,playing=false,last=0,state=null,logCount=0;
function scenario(){return D.scenarios[scenarioIndex]}
function horizon(){return scenario().horizon_minutes}
function clock(t){const m=Math.floor(t);return String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0')}
function frameIndexAt(sc,t){let lo=0,hi=sc.frames.length-1;while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(sc.frames[mid][0]<=t)lo=mid;else hi=mid-1}return lo}
function upperBound(sc,t){let lo=0,hi=sc.log.length;while(lo<hi){const mid=(lo+hi)>>1;if(sc.log[mid][0]<=t)lo=mid+1;else hi=mid}return lo}
function freshState(sc){return{k:-1,amb:sc.ambulances.map(()=>null),pat:new Map()}}
function applyFrame(st,fr){const ad=fr[11];for(let i=0;i<ad.length;i+=6)st.amb[ad[i]]=[ad[i+1],ad[i+2],ad[i+3],ad[i+4],ad[i+5]];const pd=fr[12];for(let i=0;i<pd.length;i+=4)st.pat.set(pd[i],[pd[i+1],pd[i+2],pd[i+3]])}
function seek(sc,k){if(!state||k<state.k)state=freshState(sc);while(state.k<k){state.k++;applyFrame(state,sc.frames[state.k])}}
function lerp(a,b,q){return[a[0]+(b[0]-a[0])*q,a[1]+(b[1]-a[1])*q]}
function nodeName(index){return index>=0&&index<D.names.length?D.names[index]:'—'}
function patientTag(number){return number>=0?'P'+String(number).padStart(4,'0'):''}
function logRow(sc,i){const e=sc.log[i],parts=[];
 if(e[3]>=0)parts.push('환자 '+patientTag(e[3]));
 if(e[2]>=0)parts.push('구급차 '+sc.ambulances[e[2]]);
 if(e[5]>=0){const h=sc.hospitals[e[5]];parts.push('병원 '+nodeName(h.node))}
 if(e[4]>=0)parts.push(nodeName(e[4]));
 return '<div class="sim-log-row" data-t="'+e[0]+'"><span class="lt">'+clock(e[0])+'</span><span class="lk">'+D.log_kinds[e[1]]+'</span><span class="ld">'+parts.join(' · ')+'</span></div>'}
function rebuildLog(sc,n){const box=el('sim-log');box.innerHTML='';let html='';for(let i=Math.max(0,n-LOG_MAX);i<n;i++)html+=logRow(sc,i);box.innerHTML=html;logCount=n;logWrap.scrollTop=logWrap.scrollHeight}
function updateLog(sc,t){const n=upperBound(sc,t);if(n===logCount)return;if(n<logCount){rebuildLog(sc,n);return}
 const wrap=logWrap,atBottom=wrap.scrollHeight-wrap.scrollTop-wrap.clientHeight<48;let html='';for(let i=logCount;i<n;i++)html+=logRow(sc,i);
 el('sim-log').insertAdjacentHTML('beforeend',html);logCount=n;const box=el('sim-log');while(box.childElementCount>LOG_MAX)box.removeChild(box.firstElementChild);if(atBottom)wrap.scrollTop=wrap.scrollHeight}
function ambColor(index){const p=D.palette;return p&&p.length?p[index%p.length]:'#ef5b35'}
function drawPosts(sc){LAYER_A.clearLayers();LAYER_B.clearLayers();
 const owners={};(sc.assignment||[]).forEach(pair=>{(owners[pair[1]]=owners[pair[1]]||[]).push(pair[0])});
 D.posts.forEach((post,index)=>{const mine=owners[index]||[],assigned=mine.length>0;
  const color=assigned?ambColor(mine[0]):'#94a3b8',names=mine.map(i=>sc.ambulances[i]).join(', ');
  const population=post.population==null?'—':Math.round(post.population).toLocaleString()+'명';
  L.circleMarker(D.coords[post.node],{radius:assigned?8:4,color:assigned?'#ffffff':color,weight:assigned?2:1,fillColor:color,fillOpacity:assigned?0.95:0.55})
   .bindTooltip((assigned?'배정 대기지: ':'대기지 후보: ')+post.id+' '+post.name+(assigned?' · '+names:''))
   .bindPopup('<b>'+post.id+' '+post.name+'</b><br>유형: '+post.type_label+' ('+post.source_id+')<br>칸 인구 추정: '+population
    +'<br>'+(assigned?'배정 구급차: '+names:'이 시나리오에서는 미배정')
    +'<br>격자 칸 중심점이며 실제 주차·대기 가능 여부는 검증되지 않았습니다.')
   .addTo(assigned?LAYER_B:LAYER_A)});
 (sc.assignment||[]).forEach(pair=>{const id=sc.ambulances[pair[0]],home=D.homes[id],post=D.posts[pair[1]];
  if(home==null||home===post.node)return;
  L.polyline([D.coords[home],D.coords[post.node]],{color:ambColor(pair[0]),weight:2,opacity:.85,dashArray:'6,7'})
   .bindTooltip(id+' · '+nodeName(home)+' → '+post.name+' (원소속 → 배정 대기지)').addTo(LAYER_B)})}
function drawScenarioLayers(sc){if(D.mode==='standby')drawPosts(sc);else drawCandidates(sc)}
function drawCandidates(sc){LAYER_A.clearLayers();LAYER_B.clearLayers();
 D.candidates.forEach(c=>{const on=sc.activated.indexOf(c.id)>=0,layer=on?LAYER_A:LAYER_B;
  L.marker(D.coords[c.node],{icon:L.divIcon({className:'cand-pin '+(on?'cand-on':'cand-off'),html:on?'+':'?',iconSize:[26,26]})})
   .bindTooltip((on?'신설 후보(가동): ':'계획검토 후보(미가동): ')+c.id+' '+c.name)
   .bindPopup('<b>'+c.id+' '+c.name+'</b><br>모델 가정: '+c.category_assumption+' · 응급실 '+c.capacity+'병상<br>부지 상태: '+c.land_feasibility_status+'<br>기존 공공보건시설이며 건설 가능성이 확정된 부지가 아닙니다.')
   .addTo(layer)})}
function render(t){const sc=scenario(),k=frameIndexAt(sc,t),fr=sc.frames[k];seek(sc,k);
 el('sim-time').textContent=clock(t);el('sim-range').value=t;
 el('sim-elapsed').textContent=Math.floor(t)+'분';el('sim-patients').textContent=fr[9];
 el('sim-saved').textContent=fr[6].toFixed(2);el('sim-lost').textContent=fr[7].toFixed(2);
 el('sim-resp').textContent=fr[10]>=0?fr[10].toFixed(1)+'분':'—';el('sim-waiting').textContent=fr[8];
 state.amb.forEach((u,i)=>{if(!u)return;const id=sc.ambulances[i],from=D.coords[u[0]];let p=from;
  if(u[2]>=0&&u[3]!=null&&u[4]!=null){const span=Math.max(.001,u[4]-u[3]),q=Math.max(0,Math.min(1,(t-u[3])/span));p=lerp(from,D.coords[u[2]],q)}
  const status=D.ambulance_status[u[1]],html='<div class="ambulance-pin st-'+status+'">🚑</div>';
  if(!units[id])units[id]=L.marker(p,{icon:L.divIcon({className:'',html:html,iconSize:[28,28]})}).bindTooltip(id).addTo(SIM_MAP);
  else{units[id].setLatLng(p);units[id].setIcon(L.divIcon({className:'',html:html,iconSize:[28,28]}))}
  units[id].setTooltipContent(id+' · '+status)});
 patientLayer.clearLayers();const counts={};
 state.pat.forEach(p=>{const status=D.patient_status[p[1]];if(status==='complete'||status==='lost')return;counts[p[0]]=(counts[p[0]]||0)+1});
 Object.keys(counts).forEach(node=>{L.circleMarker(D.coords[node],{radius:8,color:'#fff',weight:2,fillColor:'#dc2626',fillOpacity:.95}).bindTooltip(nodeName(Number(node))+' 대기/처리 중 환자 '+counts[node]+'명').addTo(patientLayer)});
 updateLog(sc,t)}
function switchScenario(index){scenarioIndex=index;const sc=scenario();state=null;logCount=0;el('sim-log').innerHTML='';
 Object.keys(units).forEach(id=>{SIM_MAP.removeLayer(units[id]);delete units[id]});
 el('sim-range').max=sc.horizon_minutes;drawScenarioLayers(sc);render(simT)}
function tick(now){if(!playing)return;if(!last)last=now;simT+=((now-last)/1000)*Number(el('sim-speed').value);last=now;
 if(simT>=horizon()){simT=horizon();playing=false;el('sim-play').textContent='재생'}render(simT);if(playing)requestAnimationFrame(tick)}
const select=el('sim-scenario');
D.scenarios.forEach((sc,i)=>{const option=document.createElement('option');option.value=i;option.textContent=sc.label;select.appendChild(option)});
select.onchange=e=>switchScenario(Number(e.target.value));
el('sim-play').onclick=()=>{playing=!playing;el('sim-play').textContent=playing?'일시정지':'재생';last=0;if(simT>=horizon())simT=0;if(playing)requestAnimationFrame(tick)};
el('sim-range').oninput=e=>{simT=Number(e.target.value);render(simT)};
logWrap.addEventListener('click',e=>{const row=e.target.closest('.sim-log-row');if(!row)return;playing=false;el('sim-play').textContent='재생';simT=Number(row.dataset.t);render(simT)});
switchScenario(0);
// Headless/manual QA hook: drive the playback without the mouse.
window.__sim={setTime:t=>{simT=t;render(t)},switchScenario:switchScenario,scenarioCount:D.scenarios.length};
});
'''


if __name__ == "__main__":
    raise SystemExit(main())
