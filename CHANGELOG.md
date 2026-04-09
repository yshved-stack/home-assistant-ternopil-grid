# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and the project uses Semantic Versioning for public release tags.

## [1.2.3] - 2026-04-09

### Added

- GitHub Actions validation workflow for compile, contract, and repo-asset checks
- `verify_repo_assets.py` to validate release-facing metadata, examples, and badges
- portable lease-source example JSON for standalone resolver setups

### Changed

- README now includes a validation badge, the lease example, and a clearer note about the current `house_number` limitation
- DHCP URL resolution now uses a short request timeout independent from the cache TTL

### Fixed

- changing `Device probe target` no longer silently keeps probing a stale saved IP when the newly selected entity has no HA-visible IP
- ambiguous streets with multiple outage groups no longer auto-bind to the first returned group
- changing street via the integration options flow no longer reloads the full config entry
- changing street via the `Street` select entity no longer forces an unnecessary ping refresh

## [1.2.2] - 2026-04-09

### Changed

- the HA options dialog now exposes direct menu entries for `Probe behavior`, `Device probe target`, and `Manual IP target`
- wording in the options flow was polished to make method, timing, and target settings easier to scan
- the device target picker now filters out irrelevant template sensors and keeps the probe list focused on usable targets

### Fixed

- worked around the HA frontend issue where the earlier target-source subflow did not reliably continue to the second step
- patch release version updated to `1.2.2`

## [1.2.1] - 2026-04-09

### Changed

- the Home Assistant options UI now uses searchable selector-based street picking instead of the older two-step query flow
- ping settings are now split into a base step plus dedicated `Probe device / entity` and `Manual / custom IP` follow-up steps
- probe target labels in the entity picker are cleaner and focus on friendly name, source hint, and resolved IP

### Fixed

- removed stale `pick_street` translation remnants left behind after the selector UI migration
- the integration manifest now points at the public repository and issue tracker URLs
- patch release version updated to `1.2.1`

## [1.2.0] - 2026-04-08

### Added

- config-flow support for street selection and live probe settings
- probe target selection via Home Assistant entity plus manual IP and port settings
- probe metadata in entity attributes for dashboard and automation use
- standalone example dashboard block in `examples/power-grid-card.yaml`
- HACS metadata via `hacs.json`

### Changed

- `Next scheduled change` now comes from the schedule sensor attributes instead of relying on legacy live entity ids
- rolling chart simplified to focus on `Planned` and `Actual (inferred vs schedule)`
- chart readability improved with larger hour labels, wider bars, and cleaner spacing
- street selection now refreshes coordinators in place instead of reloading the integration
- schedule context assembly now uses shared caching to avoid repeated recomputation

### Fixed

- Telegram alert flow no longer retriggers on same-street selection changes
- `off_next_24h` and next-change calculations now derive from merged schedule segments instead of the older mixed rolling window path
- standalone extraction now ships a valid, current dashboard example and public repo metadata

[1.2.0]: https://github.com/yshved-stack/home-assistant-ternopil-grid/releases/tag/v1.2.0
[1.2.1]: https://github.com/yshved-stack/home-assistant-ternopil-grid/releases/tag/v1.2.1
[1.2.2]: https://github.com/yshved-stack/home-assistant-ternopil-grid/releases/tag/v1.2.2
[1.2.3]: https://github.com/yshved-stack/home-assistant-ternopil-grid/releases/tag/v1.2.3
