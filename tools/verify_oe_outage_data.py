from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "data" / "Ternopil.json"


def fail(message: str) -> int:
    print(f"[FAIL] {message}")
    return 1


def main() -> int:
    if not DATA_PATH.exists():
        return fail("data/Ternopil.json is missing")

    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return fail("data/Ternopil.json must be a JSON object")

    for key in ("regionId", "lastUpdated", "fact", "preset"):
        if key not in payload:
            return fail(f"data/Ternopil.json is missing top-level key: {key}")

    if payload.get("regionId") != "Ternopil":
        return fail("regionId must be 'Ternopil'")

    fact = payload.get("fact")
    if not isinstance(fact, dict):
        return fail("fact must be a JSON object")

    fact_data = fact.get("data")
    if not isinstance(fact_data, dict) or not fact_data:
        return fail("fact.data must be a non-empty JSON object")

    for day_key, groups in fact_data.items():
        if not str(day_key).isdigit():
            return fail(f"fact.data key must be an epoch-like string, got: {day_key}")
        if not isinstance(groups, dict) or not groups:
            return fail(f"fact.data[{day_key}] must contain group entries")
        for group_key, hours in groups.items():
            if not str(group_key).startswith("GPV"):
                return fail(f"group key must start with GPV, got: {group_key}")
            if not isinstance(hours, dict):
                return fail(f"{group_key} hours payload must be a JSON object")
            if sorted(hours.keys(), key=int) != [str(hour) for hour in range(1, 25)]:
                return fail(f"{group_key} must define hours 1..24")

    preset = payload.get("preset")
    if not isinstance(preset, dict):
        return fail("preset must be a JSON object")

    time_zone = preset.get("time_zone")
    if not isinstance(time_zone, dict) or len(time_zone) != 24:
        return fail("preset.time_zone must contain 24 entries")

    time_type = preset.get("time_type")
    if not isinstance(time_type, dict):
        return fail("preset.time_type must be a JSON object")

    required_types = {"yes", "no", "maybe", "first", "second", "mfirst", "msecond"}
    missing = sorted(required_types - set(time_type.keys()))
    if missing:
        return fail(f"preset.time_type is missing keys: {', '.join(missing)}")

    print("OK: OE outage data payload is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
