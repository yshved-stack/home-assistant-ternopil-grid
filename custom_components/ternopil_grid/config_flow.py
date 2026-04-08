from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import fetch_building_group, fetch_streets
from .const import (
    CONF_CITY_ID,
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

_LOGGER = logging.getLogger(__name__)

_PREFIX_RE = re.compile(
    r"^\s*(?:\u0432\u0443\u043b\.?|\u0432\u0443\u043b\u0438\u0446\u044f|\u043f\u0440\u043e\u0441\u043f\.?|\u043f\u0440\u043e\u0441\u043f\u0435\u043a\u0442|\u0431\u0443\u043b\.?|\u0431\u0443\u043b\u044c\u0432\u0430\u0440|\u043f\u0440\u043e\u0432\.?|\u043f\u0440\u043e\u0432\u0443\u043b\u043e\u043a|\u043f\u043b\.?|\u043f\u043b\u043e\u0449\u0430|\u043c\u0430\u0439\u0434\.?|\u043c\u0430\u0439\u0434\u0430\u043d|\u043f\u0430\u0440\u043a)\s+",
    re.IGNORECASE,
)


def _norm_street_query(query: str) -> str:
    return _PREFIX_RE.sub("", (query or "").strip()).strip()


def _strip_prefix(name: str) -> str:
    normalized = _PREFIX_RE.sub("", name.strip()).strip()
    return normalized if normalized else name.strip()


def _street_matches_from_query(streets: list[dict[str, Any]], query: str) -> dict[str, dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    needle = query.casefold()

    for street in streets:
        full = str(street["name"]).strip()
        label = _strip_prefix(full)
        if needle in label.casefold() or needle in full.casefold():
            matches.append({"id": int(street["id"]), "label": label, "full": full})

    used: set[str] = set()
    street_matches: dict[str, dict[str, Any]] = {}
    for match in matches[:200]:
        label = match["label"]
        if label in used:
            label = match["full"]
        used.add(label)
        street_matches[label] = {"id": match["id"], "full": match["full"]}
    return street_matches


def _is_ping_candidate_entity(entity_id: str) -> bool:
    if entity_id.startswith(("binary_sensor.ternopil_grid_", "sensor.ternopil_grid_", "select.ternopil_grid_")):
        return False
    domain = entity_id.partition(".")[0]
    return domain in {"switch", "light", "binary_sensor", "device_tracker"}


def _ping_entity_options(hass: HomeAssistant, current_entity_id: str) -> dict[str, str]:
    options: dict[str, str] = {"": "Manual / custom IP"}
    candidates: list[tuple[str, str]] = []

    for state in hass.states.async_all():
        entity_id = state.entity_id
        if not _is_ping_candidate_entity(entity_id):
            continue
        friendly_name = str(state.attributes.get("friendly_name") or entity_id).strip()
        label = friendly_name if friendly_name == entity_id else f"{friendly_name} ({entity_id})"
        candidates.append((entity_id, label))

    for entity_id, label in sorted(candidates, key=lambda item: item[1].casefold()):
        options[entity_id] = label

    if current_entity_id and current_entity_id not in options:
        options[current_entity_id] = current_entity_id
    return options


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
    entity_options = _ping_entity_options(hass, normalized.get(CONF_PING_ENTITY_ID, ""))
    return vol.Schema(
        {
            vol.Optional(CONF_PING_ENABLED, default=normalized[CONF_PING_ENABLED]): bool,
            vol.Optional(CONF_PING_METHOD, default=normalized[CONF_PING_METHOD]): vol.In(PING_METHOD_OPTIONS),
            vol.Optional(CONF_PING_ENTITY_ID, default=normalized[CONF_PING_ENTITY_ID]): vol.In(entity_options),
            vol.Optional(CONF_PING_IP, default=normalized[CONF_PING_IP]): str,
            vol.Optional(CONF_PING_PORT, default=normalized[CONF_PING_PORT]): int,
            vol.Optional(
                CONF_PING_TIMEOUT,
                default=normalized[CONF_PING_TIMEOUT],
            ): vol.Coerce(float),
            vol.Optional(
                CONF_PING_INTERVAL,
                default=normalized[CONF_PING_INTERVAL],
            ): int,
            vol.Optional(CONF_PING_HTTP_SSL, default=normalized[CONF_PING_HTTP_SSL]): bool,
            vol.Optional(CONF_PING_HTTP_PATH, default=normalized[CONF_PING_HTTP_PATH]): str,
            vol.Optional(
                CONF_PING_HISTORY_HOURS,
                default=normalized[CONF_PING_HISTORY_HOURS],
            ): int,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._street_matches: dict[str, dict[str, Any]] = {}
        self._street_query: str = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self.context[CONF_HOUSE_NUMBER] = str(user_input.get(CONF_HOUSE_NUMBER, "") or "")
            self._street_query = _norm_street_query(str(user_input.get("street_query", "") or ""))

            if not self._street_query:
                errors["street_query"] = "required"
            else:
                return await self.async_step_pick_street()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("street_query", default=self._street_query): str,
                    vol.Optional(CONF_HOUSE_NUMBER, default=str(self.context.get(CONF_HOUSE_NUMBER, ""))): str,
                }
            ),
            errors=errors,
        )

    async def async_step_pick_street(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        streets = await fetch_streets(self.hass, DEFAULT_TERNOPIL_CITY_ID)
        self._street_matches = _street_matches_from_query(streets, self._street_query)

        if not self._street_matches:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("street_query", default=self._street_query): str,
                        vol.Optional(CONF_HOUSE_NUMBER, default=str(self.context.get(CONF_HOUSE_NUMBER, ""))): str,
                    }
                ),
                errors={"street_query": "no_match"},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            label = str(user_input.get("street_label"))
            match = self._street_matches.get(label)
            if not match:
                errors["street_label"] = "required"
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
            step_id="pick_street",
            data_schema=vol.Schema(
                {
                    vol.Required("street_label"): vol.In(sorted(self._street_matches.keys(), key=str.casefold)),
                }
            ),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        self._street_matches: dict[str, dict[str, Any]] = {}
        self._street_query = _strip_prefix(str(entry.data.get(CONF_STREET_NAME, "") or ""))
        self._house_number = str(entry.data.get(CONF_HOUSE_NUMBER, "") or "")

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="init", menu_options=["street", "ping"])

    async def async_step_ping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = dict(self.entry.options)
        ping_options = _normalize_ping_options(options)
        if user_input is not None:
            updated = _normalize_ping_options({**options, **user_input})
            errors: dict[str, str] = {}
            if updated[CONF_PING_ENABLED]:
                if updated[CONF_PING_METHOD] == "entity" and not updated[CONF_PING_ENTITY_ID]:
                    errors[CONF_PING_ENTITY_ID] = "required"
                if updated[CONF_PING_METHOD] != "entity" and not updated[CONF_PING_IP]:
                    errors[CONF_PING_IP] = "required"

            if not errors:
                merged = dict(self.entry.options)
                merged.update(updated)
                return self.async_create_entry(title="", data=merged)

            return self.async_show_form(
                step_id="ping",
                data_schema=_ping_schema(self.hass, updated),
                errors=errors,
            )

        return self.async_show_form(step_id="ping", data_schema=_ping_schema(self.hass, ping_options))

    async def async_step_street(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._house_number = str(user_input.get(CONF_HOUSE_NUMBER, "") or "")
            self._street_query = _norm_street_query(str(user_input.get("street_query", "") or ""))

            if not self._street_query:
                errors["street_query"] = "required"
            else:
                return await self.async_step_pick_street()

        return self.async_show_form(
            step_id="street",
            data_schema=vol.Schema(
                {
                    vol.Required("street_query", default=self._street_query): str,
                    vol.Optional(CONF_HOUSE_NUMBER, default=self._house_number): str,
                }
            ),
            errors=errors,
        )

    async def async_step_pick_street(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        streets = await fetch_streets(self.hass, int(self.entry.data.get(CONF_CITY_ID, DEFAULT_TERNOPIL_CITY_ID)))
        self._street_matches = _street_matches_from_query(streets, self._street_query)

        if not self._street_matches:
            return self.async_show_form(
                step_id="street",
                data_schema=vol.Schema(
                    {
                        vol.Required("street_query", default=self._street_query): str,
                        vol.Optional(CONF_HOUSE_NUMBER, default=self._house_number): str,
                    }
                ),
                errors={"street_query": "no_match"},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            label = str(user_input.get("street_label"))
            match = self._street_matches.get(label)
            if not match:
                errors["street_label"] = "required"
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
            step_id="pick_street",
            data_schema=vol.Schema(
                {
                    vol.Required("street_label"): vol.In(sorted(self._street_matches.keys(), key=str.casefold)),
                }
            ),
            errors=errors,
        )


async def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlowHandler:
    return OptionsFlowHandler(config_entry)
