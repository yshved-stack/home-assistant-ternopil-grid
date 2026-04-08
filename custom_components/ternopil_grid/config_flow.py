from __future__ import annotations

from ipaddress import ip_address
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import selector

from .api import fetch_building_group, fetch_streets
from .const import (
    CONF_CITY_ID,
    CONF_DEBUG_LOGGING,
    CONF_GROUP,
    CONF_HOUSE_NUMBER,
    CONF_PING_ENABLED,
    CONF_PING_ENTITY_ID,
    CONF_PING_HISTORY_HOURS,
    CONF_PING_HTTP_PATH,
    CONF_PING_HTTP_SSL,
    CONF_PING_INTERVAL,
    CONF_PING_IP,
    CONF_PING_METHOD,
    CONF_PING_PORT,
    CONF_PING_TIMEOUT,
    CONF_STREET_ID,
    CONF_STREET_NAME,
    DEFAULT_PING_ENABLED,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_PING_HISTORY_HOURS,
    DEFAULT_PING_HTTP_PATH,
    DEFAULT_PING_HTTP_SSL,
    DEFAULT_PING_INTERVAL,
    DEFAULT_PING_IP,
    DEFAULT_PING_METHOD,
    DEFAULT_PING_PORT,
    DEFAULT_PING_TIMEOUT,
    DEFAULT_TERNOPIL_CITY_ID,
    DOMAIN,
    MAX_PING_HISTORY_HOURS,
    PING_METHOD_OPTIONS,
)

PING_TARGET_SOURCE_ENTITY = "entity"
PING_TARGET_SOURCE_CUSTOM = "custom_ip"
_SMART_PLUG_KEYWORDS = (
    "plug",
    "socket",
    "outlet",
    "розетка",
    "smart plug",
    "power plug",
    "softlight",
)
_TUYA_PLATFORMS = {"tuya", "localtuya", "tuya_local"}

_PREFIX_RE = re.compile(
    r"^\s*(?:\u0432\u0443\u043b\.?|\u0432\u0443\u043b\u0438\u0446\u044f|\u043f\u0440\u043e\u0441\u043f\.?|\u043f\u0440\u043e\u0441\u043f\u0435\u043a\u0442|\u0431\u0443\u043b\.?|\u0431\u0443\u043b\u044c\u0432\u0430\u0440|\u043f\u0440\u043e\u0432\.?|\u043f\u0440\u043e\u0432\u0443\u043b\u043e\u043a|\u043f\u043b\.?|\u043f\u043b\u043e\u0449\u0430|\u043c\u0430\u0439\u0434\.?|\u043c\u0430\u0439\u0434\u0430\u043d|\u043f\u0430\u0440\u043a)\s+",
    re.IGNORECASE,
)


def _strip_prefix(name: str) -> str:
    normalized = _PREFIX_RE.sub("", name.strip()).strip()
    return normalized if normalized else name.strip()


def _street_select_options(streets: list[dict[str, Any]]) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    options: list[dict[str, str]] = []
    mapping: dict[str, dict[str, Any]] = {}
    used: set[str] = set()

    sorted_streets = sorted(
        streets,
        key=lambda item: (_strip_prefix(str(item["name"])).casefold(), str(item["name"]).casefold()),
    )
    for street in sorted_streets:
        full = str(street["name"]).strip()
        label = _strip_prefix(full)
        if label in used:
            label = full
        used.add(label)
        value = str(int(street["id"]))
        options.append({"value": value, "label": label})
        mapping[value] = {"id": int(street["id"]), "full": full}

    return options, mapping


def _select_dropdown(options: list[dict[str, str]]) -> Any:
    return selector(
        {
            "select": {
                "options": options,
                "mode": "dropdown",
                "custom_value": False,
                "sort": False,
            }
        }
    )


def _text_selector() -> Any:
    return selector({"text": {}})


def _number_selector(*, min_value: float, max_value: float, step: float, mode: str = "box") -> Any:
    return selector(
        {
            "number": {
                "min": min_value,
                "max": max_value,
                "step": step,
                "mode": mode,
            }
        }
    )


def _is_ping_candidate_entity(entity_id: str) -> bool:
    if entity_id.startswith(("binary_sensor.ternopil_grid_", "sensor.ternopil_grid_", "select.ternopil_grid_")):
        return False
    domain = entity_id.partition(".")[0]
    return domain in {"switch", "light", "binary_sensor", "device_tracker"}


def _looks_like_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _resolve_entity_probe_ip(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    if state is None:
        return ""

    for key in ("ip", "ip_address", "address", "host", "local_ip", "current_address"):
        candidate = str(state.attributes.get(key) or "").strip()
        if candidate and _looks_like_ip(candidate):
            return candidate

    state_value = str(state.state or "").strip()
    return state_value if _looks_like_ip(state_value) else ""


def _integration_label(hass: HomeAssistant, entity_id: str) -> str:
    entity_registry = er.async_get(hass)
    registry_entry = entity_registry.async_get(entity_id)
    if registry_entry is None:
        return ""

    platform = str(getattr(registry_entry, "platform", "") or "").strip()
    if platform:
        return platform

    config_entry_id = str(getattr(registry_entry, "config_entry_id", "") or "").strip()
    if config_entry_id:
        config_entry = hass.config_entries.async_get_entry(config_entry_id)
        if config_entry is not None and getattr(config_entry, "domain", None):
            return str(config_entry.domain)
    return ""


def _device_summary(hass: HomeAssistant, entity_id: str) -> tuple[str, str]:
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    registry_entry = entity_registry.async_get(entity_id)
    if registry_entry is None or not getattr(registry_entry, "device_id", None):
        return "", ""

    device_entry = device_registry.async_get(registry_entry.device_id)
    if device_entry is None:
        return "", ""

    manufacturer = str(getattr(device_entry, "manufacturer", "") or "").strip()
    model = str(getattr(device_entry, "model", "") or "").strip()
    return manufacturer, model


def _is_likely_smart_plug(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    domain = entity_id.partition(".")[0]
    integration = _integration_label(hass, entity_id).lower()
    manufacturer, model = _device_summary(hass, entity_id)

    haystacks = [
        entity_id.lower(),
        integration,
        manufacturer.lower(),
        model.lower(),
    ]
    if state is not None:
        haystacks.append(str(state.attributes.get("friendly_name") or "").lower())
        haystacks.append(str(state.attributes.get("device_class") or "").lower())

    keyword_hit = any(keyword in text for text in haystacks for keyword in _SMART_PLUG_KEYWORDS)
    tuya_hint = integration in _TUYA_PLATFORMS or "tuya" in " ".join(haystacks)
    return domain == "switch" and (keyword_hit or tuya_hint)


def _entity_picker_label(hass: HomeAssistant, entity_id: str, duplicate_names: set[str]) -> tuple[int, str]:
    state = hass.states.get(entity_id)
    friendly_name = str(state.attributes.get("friendly_name") or entity_id).strip() if state is not None else entity_id
    resolved_ip = _resolve_entity_probe_ip(hass, entity_id)
    integration = _integration_label(hass, entity_id)
    manufacturer, model = _device_summary(hass, entity_id)
    likely_plug = _is_likely_smart_plug(hass, entity_id)

    parts: list[str] = []
    if likely_plug:
        parts.append("Recommended")
    parts.append(friendly_name)
    source_bits = [part for part in (manufacturer, model) if part]
    if friendly_name.casefold() in duplicate_names and entity_id:
        source_bits.append(entity_id)
    elif integration:
        source_bits.append(integration.upper())
    if source_bits:
        parts.append(" / ".join(source_bits))
    if resolved_ip:
        parts.append(resolved_ip)
    label = " · ".join(parts)

    priority = 0 if likely_plug else 1
    return priority, label


def _ping_entity_options(hass: HomeAssistant, current_entity_id: str) -> dict[str, str]:
    options: dict[str, str] = {}
    candidates: list[tuple[int, str, str]] = []
    name_counts: dict[str, int] = {}

    for state in hass.states.async_all():
        entity_id = state.entity_id
        if not _is_ping_candidate_entity(entity_id):
            continue
        friendly_name = str(state.attributes.get("friendly_name") or entity_id).strip().casefold()
        name_counts[friendly_name] = name_counts.get(friendly_name, 0) + 1

    duplicate_names = {name for name, count in name_counts.items() if count > 1}

    for state in hass.states.async_all():
        entity_id = state.entity_id
        if not _is_ping_candidate_entity(entity_id):
            continue
        priority, label = _entity_picker_label(hass, entity_id, duplicate_names)
        candidates.append((priority, label.casefold(), entity_id))

    for _, _, entity_id in sorted(candidates):
        options[entity_id] = _entity_picker_label(hass, entity_id, duplicate_names)[1]

    if current_entity_id and current_entity_id not in options:
        options[current_entity_id] = current_entity_id
    return options


def _ping_target_source(options: dict[str, Any]) -> str:
    return PING_TARGET_SOURCE_ENTITY if str(options.get(CONF_PING_ENTITY_ID, "") or "").strip() else PING_TARGET_SOURCE_CUSTOM


def _ping_target_source_options() -> list[dict[str, str]]:
    return [
        {"value": PING_TARGET_SOURCE_ENTITY, "label": "Probe device / entity"},
        {"value": PING_TARGET_SOURCE_CUSTOM, "label": "Manual / custom IP"},
    ]


def _autofill_ping_options(hass: HomeAssistant, options: dict[str, Any]) -> dict[str, Any]:
    updated = dict(options)
    entity_id = str(updated.get(CONF_PING_ENTITY_ID, "") or "").strip()
    method = str(updated.get(CONF_PING_METHOD, DEFAULT_PING_METHOD) or DEFAULT_PING_METHOD).lower().strip()
    resolved_ip = _resolve_entity_probe_ip(hass, entity_id) if entity_id else ""

    current_ip = str(updated.get(CONF_PING_IP, "") or "").strip()
    if resolved_ip and current_ip in {"", DEFAULT_PING_IP}:
        updated[CONF_PING_IP] = resolved_ip

    current_port = int(updated.get(CONF_PING_PORT, DEFAULT_PING_PORT) or 0)
    if method == "http" and current_port in {0, DEFAULT_PING_PORT, 443}:
        updated[CONF_PING_PORT] = 443 if bool(updated.get(CONF_PING_HTTP_SSL, DEFAULT_PING_HTTP_SSL)) else 80
    elif method == "tcp" and current_port <= 0:
        updated[CONF_PING_PORT] = DEFAULT_PING_PORT

    return updated


def _normalize_ping_options(options: dict[str, Any]) -> dict[str, Any]:
    method = str(options.get(CONF_PING_METHOD, DEFAULT_PING_METHOD) or DEFAULT_PING_METHOD).lower().strip()
    if method not in PING_METHOD_OPTIONS:
        method = DEFAULT_PING_METHOD

    path = str(options.get(CONF_PING_HTTP_PATH, DEFAULT_PING_HTTP_PATH) or DEFAULT_PING_HTTP_PATH).strip() or "/"
    if not path.startswith("/"):
        path = "/" + path

    return {
        CONF_PING_ENABLED: bool(options.get(CONF_PING_ENABLED, DEFAULT_PING_ENABLED)),
        CONF_PING_METHOD: method,
        CONF_PING_ENTITY_ID: str(options.get(CONF_PING_ENTITY_ID, "") or "").strip(),
        CONF_PING_IP: str(options.get(CONF_PING_IP, DEFAULT_PING_IP) or "").strip(),
        CONF_PING_PORT: max(0, min(65535, int(options.get(CONF_PING_PORT, DEFAULT_PING_PORT) or 0))),
        CONF_PING_TIMEOUT: max(
            0.2,
            float(options.get(CONF_PING_TIMEOUT, DEFAULT_PING_TIMEOUT) or DEFAULT_PING_TIMEOUT),
        ),
        CONF_PING_INTERVAL: max(1, int(options.get(CONF_PING_INTERVAL, DEFAULT_PING_INTERVAL) or DEFAULT_PING_INTERVAL)),
        CONF_PING_HTTP_SSL: bool(options.get(CONF_PING_HTTP_SSL, DEFAULT_PING_HTTP_SSL)),
        CONF_PING_HTTP_PATH: path,
        CONF_PING_HISTORY_HOURS: max(
            1,
            min(
                MAX_PING_HISTORY_HOURS,
                int(options.get(CONF_PING_HISTORY_HOURS, DEFAULT_PING_HISTORY_HOURS) or DEFAULT_PING_HISTORY_HOURS),
            ),
        ),
    }


def _ping_schema(hass: HomeAssistant, options: dict[str, Any]) -> vol.Schema:
    normalized = _normalize_ping_options(options)
    return vol.Schema(
        {
            vol.Optional(CONF_PING_ENABLED, default=normalized[CONF_PING_ENABLED]): bool,
            vol.Optional(CONF_PING_METHOD, default=normalized[CONF_PING_METHOD]): vol.In(PING_METHOD_OPTIONS),
            vol.Optional("ping_target_source", default=_ping_target_source(normalized)): _select_dropdown(
                _ping_target_source_options()
            ),
            vol.Optional(
                CONF_PING_TIMEOUT,
                default=normalized[CONF_PING_TIMEOUT],
            ): _number_selector(min_value=0.2, max_value=10, step=0.1),
            vol.Optional(
                CONF_PING_INTERVAL,
                default=normalized[CONF_PING_INTERVAL],
            ): _number_selector(min_value=1, max_value=300, step=1),
            vol.Optional(
                CONF_PING_HISTORY_HOURS,
                default=normalized[CONF_PING_HISTORY_HOURS],
            ): _number_selector(min_value=1, max_value=MAX_PING_HISTORY_HOURS, step=1),
        }
    )


def _ping_entity_schema(hass: HomeAssistant, options: dict[str, Any]) -> vol.Schema:
    normalized = _normalize_ping_options(options)
    entity_options = _ping_entity_options(hass, normalized.get(CONF_PING_ENTITY_ID, ""))
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_PING_ENTITY_ID,
            default=normalized[CONF_PING_ENTITY_ID],
        ): _select_dropdown(
            [{"value": value, "label": label} for value, label in entity_options.items()]
        ),
    }
    if normalized[CONF_PING_METHOD] in {"tcp", "http"}:
        schema[vol.Optional(CONF_PING_PORT, default=normalized[CONF_PING_PORT])] = _number_selector(
            min_value=1,
            max_value=65535,
            step=1,
        )
    if normalized[CONF_PING_METHOD] == "http":
        schema[vol.Optional(CONF_PING_HTTP_SSL, default=normalized[CONF_PING_HTTP_SSL])] = bool
        schema[vol.Optional(CONF_PING_HTTP_PATH, default=normalized[CONF_PING_HTTP_PATH])] = _text_selector()
    return vol.Schema(schema)


def _ping_manual_schema(options: dict[str, Any]) -> vol.Schema:
    normalized = _normalize_ping_options(options)
    schema: dict[Any, Any] = {
        vol.Required(CONF_PING_IP, default=normalized[CONF_PING_IP]): _text_selector(),
    }
    if normalized[CONF_PING_METHOD] in {"tcp", "http"}:
        schema[vol.Optional(CONF_PING_PORT, default=normalized[CONF_PING_PORT])] = _number_selector(
            min_value=1,
            max_value=65535,
            step=1,
        )
    if normalized[CONF_PING_METHOD] == "http":
        schema[vol.Optional(CONF_PING_HTTP_SSL, default=normalized[CONF_PING_HTTP_SSL])] = bool
        schema[vol.Optional(CONF_PING_HTTP_PATH, default=normalized[CONF_PING_HTTP_PATH])] = _text_selector()
    return vol.Schema(schema)


def _diagnostics_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_DEBUG_LOGGING,
                default=bool(options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)),
            ): bool,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "OptionsFlowHandler":
        return OptionsFlowHandler(config_entry)

    def __init__(self) -> None:
        self._streets: list[dict[str, Any]] | None = None

    async def _async_get_streets(self) -> list[dict[str, Any]]:
        if self._streets is None:
            self._streets = await fetch_streets(self.hass, DEFAULT_TERNOPIL_CITY_ID)
        return self._streets

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        streets = await self._async_get_streets()
        street_options, street_map = _street_select_options(streets)

        if user_input is not None:
            street_key = str(user_input.get(CONF_STREET_ID, "") or "").strip()
            self.context[CONF_HOUSE_NUMBER] = str(user_input.get(CONF_HOUSE_NUMBER, "") or "").strip()
            match = street_map.get(street_key)
            if match is None:
                errors[CONF_STREET_ID] = "required"
            else:
                street_id = int(match["id"])
                street_name = str(match["full"])
                house_number = str(self.context.get(CONF_HOUSE_NUMBER, "") or "").strip()

                group = await fetch_building_group(self.hass, DEFAULT_TERNOPIL_CITY_ID, street_id)
                title = street_name if not house_number else f"{street_name}, {house_number}"

                await self.async_set_unique_id(f"{DEFAULT_TERNOPIL_CITY_ID}/{street_id}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_CITY_ID: DEFAULT_TERNOPIL_CITY_ID,
                        CONF_STREET_ID: street_id,
                        CONF_STREET_NAME: street_name,
                        CONF_HOUSE_NUMBER: house_number,
                        CONF_GROUP: group or "",
                    },
                    options={
                        CONF_STREET_ID: street_id,
                        CONF_DEBUG_LOGGING: DEFAULT_DEBUG_LOGGING,
                        CONF_PING_ENABLED: DEFAULT_PING_ENABLED,
                        CONF_PING_ENTITY_ID: "",
                        CONF_PING_IP: DEFAULT_PING_IP,
                        CONF_PING_INTERVAL: DEFAULT_PING_INTERVAL,
                        CONF_PING_METHOD: DEFAULT_PING_METHOD,
                        CONF_PING_PORT: DEFAULT_PING_PORT,
                        CONF_PING_TIMEOUT: DEFAULT_PING_TIMEOUT,
                        CONF_PING_HTTP_SSL: DEFAULT_PING_HTTP_SSL,
                        CONF_PING_HTTP_PATH: DEFAULT_PING_HTTP_PATH,
                        CONF_PING_HISTORY_HOURS: DEFAULT_PING_HISTORY_HOURS,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STREET_ID): _select_dropdown(street_options),
                    vol.Optional(CONF_HOUSE_NUMBER, default=str(self.context.get(CONF_HOUSE_NUMBER, ""))): _text_selector(),
                }
            ),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        self._house_number = str(entry.data.get(CONF_HOUSE_NUMBER, "") or "")
        self._streets: list[dict[str, Any]] | None = None
        self._ping_working_options: dict[str, Any] = {}

    async def _async_get_streets(self) -> list[dict[str, Any]]:
        if self._streets is None:
            self._streets = await fetch_streets(self.hass, int(self.entry.data.get(CONF_CITY_ID, DEFAULT_TERNOPIL_CITY_ID)))
        return self._streets

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="init", menu_options=["street", "ping", "diagnostics"])

    async def async_step_ping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = dict(self.entry.options)
        ping_options = _normalize_ping_options(options)
        if user_input is not None:
            updated = _normalize_ping_options({**options, **user_input})
            errors: dict[str, str] = {}
            target_source = str(user_input.get("ping_target_source", _ping_target_source(updated)) or "").strip()
            if target_source not in {PING_TARGET_SOURCE_ENTITY, PING_TARGET_SOURCE_CUSTOM}:
                errors["ping_target_source"] = "required"
            if updated[CONF_PING_ENABLED] and target_source == PING_TARGET_SOURCE_CUSTOM and updated[CONF_PING_METHOD] == "entity":
                errors[CONF_PING_METHOD] = "invalid_target_source"

            if not errors:
                self._ping_working_options = dict(self.entry.options)
                self._ping_working_options.update(updated)
                if not updated[CONF_PING_ENABLED]:
                    return self.async_create_entry(title="", data=self._ping_working_options)
                if target_source == PING_TARGET_SOURCE_ENTITY:
                    return await self.async_step_ping_entity()
                self._ping_working_options[CONF_PING_ENTITY_ID] = ""
                return await self.async_step_ping_manual()

            return self.async_show_form(
                step_id="ping",
                data_schema=_ping_schema(self.hass, updated),
                errors=errors,
            )

        return self.async_show_form(step_id="ping", data_schema=_ping_schema(self.hass, ping_options))

    async def async_step_ping_entity(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = _normalize_ping_options(self._ping_working_options or dict(self.entry.options))
        if user_input is not None:
            updated = _normalize_ping_options({**options, **user_input})
            updated = _autofill_ping_options(self.hass, updated)
            errors: dict[str, str] = {}
            if not updated[CONF_PING_ENTITY_ID]:
                errors[CONF_PING_ENTITY_ID] = "required"
            if not errors:
                merged = dict(self.entry.options)
                merged.update(updated)
                return self.async_create_entry(title="", data=merged)
            return self.async_show_form(
                step_id="ping_entity",
                data_schema=_ping_entity_schema(self.hass, updated),
                errors=errors,
            )

        return self.async_show_form(step_id="ping_entity", data_schema=_ping_entity_schema(self.hass, options))

    async def async_step_ping_manual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = _normalize_ping_options(self._ping_working_options or dict(self.entry.options))
        if user_input is not None:
            updated = _normalize_ping_options({**options, **user_input, CONF_PING_ENTITY_ID: ""})
            errors: dict[str, str] = {}
            if not updated[CONF_PING_IP]:
                errors[CONF_PING_IP] = "required"
            if not errors:
                merged = dict(self.entry.options)
                merged.update(updated)
                return self.async_create_entry(title="", data=merged)
            return self.async_show_form(
                step_id="ping_manual",
                data_schema=_ping_manual_schema(updated),
                errors=errors,
            )

        return self.async_show_form(step_id="ping_manual", data_schema=_ping_manual_schema(options))

    async def async_step_diagnostics(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = dict(self.entry.options)
        if user_input is not None:
            merged = dict(self.entry.options)
            merged[CONF_DEBUG_LOGGING] = bool(user_input.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING))
            return self.async_create_entry(title="", data=merged)

        return self.async_show_form(
            step_id="diagnostics",
            data_schema=_diagnostics_schema(options),
        )

    async def async_step_street(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        streets = await self._async_get_streets()
        street_options, street_map = _street_select_options(streets)
        current_street_id = str(self.entry.data.get(CONF_STREET_ID, self.entry.options.get(CONF_STREET_ID, "")) or "")

        if user_input is not None:
            self._house_number = str(user_input.get(CONF_HOUSE_NUMBER, "") or "").strip()
            street_key = str(user_input.get(CONF_STREET_ID, "") or "").strip()
            match = street_map.get(street_key)
            if match is None:
                errors[CONF_STREET_ID] = "required"
            else:
                street_id = int(match["id"])
                street_name = str(match["full"])
                group = await fetch_building_group(
                    self.hass,
                    int(self.entry.data.get(CONF_CITY_ID, DEFAULT_TERNOPIL_CITY_ID)),
                    street_id,
                )

                data = dict(self.entry.data)
                data.update(
                    {
                        CONF_STREET_ID: street_id,
                        CONF_STREET_NAME: street_name,
                        CONF_HOUSE_NUMBER: self._house_number.strip(),
                        CONF_GROUP: group or "",
                    }
                )
                options = dict(self.entry.options)
                options[CONF_STREET_ID] = street_id

                title = street_name if not self._house_number.strip() else f"{street_name}, {self._house_number.strip()}"
                self.hass.config_entries.async_update_entry(self.entry, data=data, options=options, title=title)
                self.hass.async_create_task(self.hass.config_entries.async_reload(self.entry.entry_id))
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="street",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STREET_ID, default=current_street_id): _select_dropdown(street_options),
                    vol.Optional(CONF_HOUSE_NUMBER, default=self._house_number): _text_selector(),
                }
            ),
            errors=errors,
        )
