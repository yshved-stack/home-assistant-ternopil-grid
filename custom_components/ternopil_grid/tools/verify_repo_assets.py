"""Verify release-facing repository assets for the standalone package.

Run from the standalone repo root or from the integration folder:

    python custom_components/ternopil_grid/tools/verify_repo_assets.py

Exit codes:
- 0: OK
- 2: Validation failed
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parent
REPO_ROOT = HERE.parents[2]

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_file(path: Path, failures: list[str]) -> None:
    if not path.exists():
        failures.append(f"missing file: {path}")


def main() -> int:
    failures: list[str] = []

    manifest_path = INTEGRATION / "manifest.json"
    en_path = INTEGRATION / "translations" / "en.json"
    uk_path = INTEGRATION / "translations" / "uk.json"
    hacs_path = REPO_ROOT / "hacs.json"
    readme_path = REPO_ROOT / "README.md"
    changelog_path = REPO_ROOT / "CHANGELOG.md"
    lease_example_path = REPO_ROOT / "examples" / "lease-source.example.json"

    for path in (
        manifest_path,
        en_path,
        uk_path,
        hacs_path,
        readme_path,
        changelog_path,
        lease_example_path,
    ):
        _require_file(path, failures)

    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 2

    manifest = _read_json(manifest_path)
    en = _read_json(en_path)
    uk = _read_json(uk_path)
    hacs = _read_json(hacs_path)
    lease_example = _read_json(lease_example_path)

    if not isinstance(manifest, dict):
        failures.append("manifest.json is not a JSON object")
    if not isinstance(en, dict):
        failures.append("translations/en.json is not a JSON object")
    if not isinstance(uk, dict):
        failures.append("translations/uk.json is not a JSON object")
    if not isinstance(hacs, dict):
        failures.append("hacs.json is not a JSON object")
    if not isinstance(lease_example, list):
        failures.append("examples/lease-source.example.json must be a JSON list")

    version = str((manifest or {}).get("version", "")).strip() if isinstance(manifest, dict) else ""
    if not SEMVER_RE.match(version):
        failures.append(f"manifest version is not semver: {version!r}")

    changelog_text = changelog_path.read_text(encoding="utf-8")
    if version and f"## [{version}]" not in changelog_text:
        failures.append(f"CHANGELOG.md does not contain heading for version {version}")

    readme_text = readme_path.read_text(encoding="utf-8")
    if version and f"v{version}" not in readme_text:
        failures.append(f"README.md does not mention current release tag v{version}")
    if "actions/workflows/validate.yml" not in readme_text:
        failures.append("README.md is missing the validate workflow badge/link")

    if isinstance(hacs, dict):
        if str(hacs.get("name", "")).strip() != "Ternopil Grid Schedule":
            failures.append("hacs.json name does not match integration name")
        if bool(hacs.get("render_readme")) is not True:
            failures.append("hacs.json render_readme must be true")

    if isinstance(lease_example, list):
        for idx, item in enumerate(lease_example):
            if not isinstance(item, dict):
                failures.append(f"lease example item #{idx} is not an object")
                continue
            ip_value = str(item.get("ip", "")).strip()
            if not ip_value:
                failures.append(f"lease example item #{idx} is missing ip")

    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 2

    print("OK: repository assets are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
