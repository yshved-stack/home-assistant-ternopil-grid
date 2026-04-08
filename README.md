[![GitHub Release](https://img.shields.io/github/v/release/yshved-stack/home-assistant-ternopil-grid?sort=semver)](https://github.com/yshved-stack/home-assistant-ternopil-grid/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License](https://img.shields.io/github/license/yshved-stack/home-assistant-ternopil-grid)](./LICENSE)

# home-assistant-ternopil-grid

Public repository staging copy for the Home Assistant custom integration that tracks Ternopil outage schedules and compares them against a live power probe.

Intended public repo name: `home-assistant-ternopil-grid`.

## Included

- `custom_components/ternopil_grid/`
  - the integration source
  - translations
  - diagnostics
  - config flow
  - probe logic
  - the const-contract check script
- `examples/power-grid-card.yaml`
  - the current `Power Grid` dashboard block
- `LICENSE`
- `CHANGELOG.md`

## Not Included

- HomeLAB-wide dashboards outside the `Power Grid` block
- shared HomeLAB Telegram bridge packages
- Proxmox outage automation packages
- NAS/router deploy scripts

Those stay in the main `HomeLAB` repo because they are environment-specific.

## Project Tree

```text
.
|-- LICENSE
|-- README.md
|-- .gitignore
|-- CHANGELOG.md
|-- hacs.json
|-- custom_components/
|   `-- ternopil_grid/
|       |-- __init__.py
|       |-- api.py
|       |-- binary_sensor.py
|       |-- config_flow.py
|       |-- const.py
|       |-- CONTRACT.md
|       |-- coordinator.py
|       |-- diagnostics.py
|       |-- manifest.json
|       |-- ping.py
|       |-- select.py
|       |-- sensor.py
|       |-- tools/
|       `-- translations/
`-- examples/
    `-- power-grid-card.yaml
```

## Local Validation

```powershell
python -m compileall .\custom_components\ternopil_grid
python .\custom_components\ternopil_grid\tools\verify_const_contract.py
```

## Home Assistant Install

1. Copy `custom_components/ternopil_grid` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.
3. Add the `Ternopil Grid Schedule` integration from the UI.
4. Optionally import or adapt `examples/power-grid-card.yaml` into your dashboard.

## HACS

Add this repo to HACS as a custom integration repository:

- Repository: `yshved-stack/home-assistant-ternopil-grid`
- Category: `Integration`

## Releases

- release tags follow the integration version from `custom_components/ternopil_grid/manifest.json`
- current public release tag: `v1.2.2`
- human-readable release notes live in [CHANGELOG.md](./CHANGELOG.md)

## Notes

- Street selection stays in the integration settings and as a config `select` entity.
- The live power probe can use ICMP, TCP, HTTP, or entity-state checks.
- The dashboard block is intentionally focused on the outage view only; broader homelab logic belongs in the parent repo.
