from __future__ import annotations

import argparse
import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE = "https://api-poweron.toe.com.ua"
API = f"{BASE}/api"
ORIGIN = "https://poweron.toe.com.ua"
REFERER = "https://poweron.toe.com.ua/"
CITY_ID = 1032
REGION_ID = "Ternopil"
REGION_NAME = "Ternopiloblenerho"
OUTPUT_BASENAME = "Ternopil.json"
MAX_WORKERS = 12


def _resolve_kyiv_tz() -> ZoneInfo:
    for key in ("Europe/Kyiv", "Europe/Kiev"):
        try:
            return ZoneInfo(key)
        except ZoneInfoNotFoundError:
            continue
    fallback = datetime.now().astimezone().tzinfo
    if fallback is None:
        raise RuntimeError("No Kyiv timezone database entry found and no local timezone fallback is available")
    return fallback


KYIV_TZ = _resolve_kyiv_tz()

TIME_TYPE_MAP: dict[tuple[str, str], str] = {
    ("0", "0"): "yes",
    ("1", "1"): "no",
    ("10", "10"): "maybe",
    ("1", "0"): "first",
    ("0", "1"): "second",
    ("10", "0"): "mfirst",
    ("0", "10"): "msecond",
    ("1", "10"): "first",
    ("10", "1"): "second",
}


def _request_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    request_headers = {
        "Accept": "application/ld+json",
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": "ternopil-grid-oe-outage-export/1.0",
    }
    if headers:
        request_headers.update(headers)

    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def _build_url(path: str, params: dict[str, str | list[str]]) -> str:
    return f"{API}/{path}?{urlencode(params, doseq=True)}"


def _fetch_streets() -> list[dict[str, Any]]:
    payload = _request_json(_build_url("pw_streets", {"pagination": "false", "city.id": str(CITY_ID)}))
    members = payload.get("hydra:member") or []
    streets: list[dict[str, Any]] = []
    for item in members:
        if not isinstance(item, dict):
            continue
        street_id = item.get("id")
        name = item.get("name")
        if isinstance(street_id, int) and isinstance(name, str) and name.strip():
            streets.append({"id": street_id, "name": name.strip()})
    if not streets:
        raise RuntimeError("PowerOn streets API returned no streets")
    return streets


def _fetch_street_groups(street_id: int) -> list[str]:
    payload = _request_json(
        _build_url(
            "pw-accounts/building-groups",
            {"cityId": str(CITY_ID), "streetId": str(street_id)},
        )
    )
    members = payload.get("hydra:member") or payload.get("buildingGroups") or []
    groups: list[str] = []
    for item in members:
        if isinstance(item, dict):
            value = item.get("chergGpv") or item.get("group") or item.get("code")
        else:
            value = item
        if isinstance(value, str) and value.strip():
            groups.append(value.strip())
    return sorted(set(groups), key=_group_sort_key)


def _group_sort_key(value: str) -> tuple[int, ...]:
    cleaned = value.replace("GPV", "").strip()
    parts = []
    for piece in cleaned.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(999)
    return tuple(parts)


def _discover_group_candidates() -> dict[str, list[int]]:
    candidates: dict[str, list[int]] = {}
    streets = _fetch_streets()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(_fetch_street_groups, int(street["id"])): int(street["id"])
            for street in streets
        }
        for future in as_completed(future_map):
            street_id = future_map[future]
            groups = future.result()
            for group in groups:
                candidates.setdefault(group, []).append(street_id)

    if not candidates:
        raise RuntimeError("Could not discover any outage groups from streets API")
    return {
        group: sorted(set(street_ids))
        for group, street_ids in sorted(candidates.items(), key=lambda item: _group_sort_key(item[0]))
    }


def _debug_key(city_id: int, street_id: int) -> str:
    return base64.b64encode(f"{city_id}/{street_id}".encode("utf-8")).decode("ascii")


def _fetch_group_schedule(street_id: int, group: str) -> list[dict[str, Any]]:
    now_utc = datetime.now(UTC)
    day0 = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day2 = day0 + timedelta(days=2)
    payload = _request_json(
        _build_url(
            "a_gpv_g",
            {
                "after": day0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "before": day2.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "group[]": [group],
                "time": f"{CITY_ID}{street_id}",
            },
        ),
        headers={"x-debug-key": _debug_key(CITY_ID, street_id)},
    )
    members = payload.get("hydra:member") or []
    if not isinstance(members, list) or not members:
        raise RuntimeError(f"Schedule API returned no members for group {group} via street {street_id}")
    return members


def _fetch_group_schedule_with_fallback(group: str, candidate_street_ids: list[int]) -> tuple[int, list[dict[str, Any]]]:
    last_error: Exception | None = None
    for street_id in candidate_street_ids:
        try:
            members = _fetch_group_schedule(street_id, group)
        except Exception as err:  # noqa: BLE001
            last_error = err
            continue
        return street_id, members
    if last_error is not None:
        raise RuntimeError(f"Could not fetch schedule for group {group}: {last_error}") from last_error
    raise RuntimeError(f"Could not fetch schedule for group {group}: no candidate streets")


def _local_midnight_epoch(date_graph: str) -> int:
    dt_utc = datetime.fromisoformat(date_graph.replace("Z", "+00:00"))
    local_date = dt_utc.astimezone(KYIV_TZ).date()
    local_midnight = datetime(local_date.year, local_date.month, local_date.day, tzinfo=KYIV_TZ)
    return int(local_midnight.astimezone(UTC).timestamp())


def _hour_status(times: dict[str, str], hour_index: int) -> str:
    hour = hour_index - 1
    first_half = str(times.get(f"{hour:02d}:00", "0"))
    second_half = str(times.get(f"{hour:02d}:30", "0"))
    return TIME_TYPE_MAP.get((first_half, second_half), "maybe")


def _time_zone_preset() -> dict[str, list[str]]:
    preset: dict[str, list[str]] = {}
    for hour in range(24):
        next_hour = hour + 1
        preset[str(next_hour)] = [
            f"{hour:02d}-{next_hour:02d}",
            f"{hour:02d}:00",
            "24:00" if next_hour == 24 else f"{next_hour:02d}:00",
        ]
    return preset


def _build_payload(group_candidates: dict[str, list[int]]) -> tuple[dict[str, Any], dict[str, int]]:
    fact_data: dict[str, dict[str, dict[str, str]]] = {}
    representatives: dict[str, int] = {}

    for group, candidate_street_ids in group_candidates.items():
        street_id, schedule_members = _fetch_group_schedule_with_fallback(group, candidate_street_ids)
        representatives[group] = street_id
        group_key = f"GPV{group}"
        for item in schedule_members:
            if not isinstance(item, dict):
                continue
            date_graph = item.get("dateGraph")
            data_json = item.get("dataJson")
            if not isinstance(date_graph, str) or not isinstance(data_json, dict):
                continue
            group_data = data_json.get(group)
            if not isinstance(group_data, dict):
                continue
            times = group_data.get("times")
            if not isinstance(times, dict) or not times:
                continue

            date_key = str(_local_midnight_epoch(date_graph))
            fact_data.setdefault(date_key, {})
            fact_data[date_key][group_key] = {
                str(hour): _hour_status(times, hour) for hour in range(1, 25)
            }

    if not fact_data:
        raise RuntimeError("No schedule payload could be built from PowerOn data")

    now_local = datetime.now(KYIV_TZ)
    today_local_midnight = datetime(now_local.year, now_local.month, now_local.day, tzinfo=KYIV_TZ)

    payload = {
        "regionId": REGION_ID,
        "lastUpdated": now_local.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "fact": {
            "data": dict(sorted(fact_data.items(), key=lambda item: int(item[0]))),
            "update": now_local.strftime("%d.%m.%Y %H:%M"),
            "today": int(today_local_midnight.astimezone(UTC).timestamp()),
        },
        "preset": {
            "time_zone": _time_zone_preset(),
            "time_type": {
                "yes": "Світло є",
                "no": "Світла немає",
                "maybe": "Можливе відключення",
                "first": "Світла не буде перші 30 хв.",
                "second": "Світла не буде другі 30 хв",
                "mfirst": "Можливе відключення перші 30 хв.",
                "msecond": "Можливе відключення другі 30 хв.",
            },
        },
    }
    return payload, representatives


def _normalized_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    normalized["lastUpdated"] = ""
    fact = normalized.get("fact")
    if isinstance(fact, dict):
        fact["update"] = ""
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Ternopil outage data in OE_OUTAGE_DATA format.")
    parser.add_argument(
        "--output",
        default=str(Path("data") / OUTPUT_BASENAME),
        help="Output file path.",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    group_candidates = _discover_group_candidates()
    payload, representatives = _build_payload(group_candidates)

    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if _normalized_payload(existing) == _normalized_payload(payload):
            print(f"no data change: {output_path}")
            return 0

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ": ")),
        encoding="utf-8",
    )
    print(f"updated: {output_path}")
    print(f"groups: {', '.join(representatives)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
