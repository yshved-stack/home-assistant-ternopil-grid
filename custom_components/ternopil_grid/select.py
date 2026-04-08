from __future__ import annotations

import logging
import re

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import fetch_building_group, fetch_building_groups, fetch_streets
from .const import (
    CONF_CITY_ID,
    CONF_GROUP,
    CONF_HOUSE_NUMBER,
    CONF_STREET_ID,
    CONF_STREET_NAME,
    DEFAULT_TERNOPIL_CITY_ID,
    DOMAIN,
    ENTITY_PREFIX,
    STORE_LEGACY_PING,
    STORE_LEGACY_SCHEDULE,
    STORE_PING_COORDINATOR,
    STORE_SCHEDULE_COORDINATOR,
)

_LOGGER = logging.getLogger(__name__)

_PREFIX_RE = re.compile(
    r"^\s*(?:\u0432\u0443\u043b\.?|\u0432\u0443\u043b\u0438\u0446\u044f|\u043f\u0440\u043e\u0441\u043f\.?|\u043f\u0440\u043e\u0441\u043f\u0435\u043a\u0442|\u0431\u0443\u043b\.?|\u0431\u0443\u043b\u044c\u0432\u0430\u0440|\u043f\u0440\u043e\u0432\.?|\u043f\u0440\u043e\u0432\u0443\u043b\u043e\u043a|\u043f\u043b\.?|\u043f\u043b\u043e\u0449\u0430|\u043c\u0430\u0439\u0434\.?|\u043c\u0430\u0439\u0434\u0430\u043d|\u043f\u0430\u0440\u043a)\s+",
    re.IGNORECASE,
)


def _strip_prefix(name: str) -> str:
    normalized = _PREFIX_RE.sub("", name.strip()).strip()
    return normalized if normalized else name.strip()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(
        [
            TernopilStreetSelect(hass, entry),
            TernopilOutageGroupSelect(hass, entry),
        ],
        update_before_add=True,
    )


class _BaseTernopilSelect(SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

    async def async_update(self) -> None:
        await self._refresh_options()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.entry.add_update_listener(self._async_handle_entry_update))

    async def _async_handle_entry_update(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        await self._refresh_options()
        self.async_write_ha_state()

    async def _refresh_options(self) -> None:
        raise NotImplementedError


class TernopilStreetSelect(_BaseTernopilSelect):
    _attr_icon = "mdi:alpha-a-circle"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_street"
        self._attr_name = f"{ENTITY_PREFIX} Street"
        self._attr_options = []
        self._id_to_label: dict[int, str] = {}
        self._id_to_full: dict[int, str] = {}
        self._label_to_id: dict[str, int] = {}
        self._current_label: str | None = None

    def _current_street_id(self) -> int:
        return int(self.entry.options.get(CONF_STREET_ID, self.entry.data.get(CONF_STREET_ID, 0)) or 0)

    def _current_street_name(self) -> str:
        return str(self.entry.data.get(CONF_STREET_NAME, "") or "").strip()

    async def _refresh_options(self) -> None:
        city_id = int(self.entry.data.get(CONF_CITY_ID, DEFAULT_TERNOPIL_CITY_ID))
        current_id = self._current_street_id()
        current_name = self._current_street_name()

        try:
            streets = await fetch_streets(self.hass, city_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Street list refresh failed: %s", err)
            streets = []

        used: set[str] = set()
        id_to_label: dict[int, str] = {}
        id_to_full: dict[int, str] = {}
        label_to_id: dict[str, int] = {}

        for street in streets:
            sid = int(street["id"])
            full = str(street["name"]).strip()
            label = _strip_prefix(full)
            if label in used:
                label = full
            used.add(label)
            id_to_label[sid] = label
            id_to_full[sid] = full
            label_to_id[label] = sid

        if current_id and current_name and current_id not in id_to_label:
            label = _strip_prefix(current_name)
            if label in used:
                label = current_name
            id_to_label[current_id] = label
            id_to_full[current_id] = current_name
            label_to_id[label] = current_id
        elif current_id and current_id not in id_to_label:
            label = str(current_id)
            id_to_label[current_id] = label
            id_to_full[current_id] = label
            label_to_id[label] = current_id

        self._id_to_label = id_to_label
        self._id_to_full = id_to_full
        self._label_to_id = label_to_id
        self._attr_options = sorted(self._label_to_id.keys(), key=str.casefold)
        self._current_label = self._id_to_label.get(current_id) or self._current_label

    @property
    def current_option(self) -> str | None:
        return self._id_to_label.get(self._current_street_id()) or self._current_label

    async def async_select_option(self, option: str) -> None:
        if option not in self._label_to_id:
            return

        street_id = self._label_to_id[option]
        if street_id == self._current_street_id() and option == (self.current_option or ""):
            return
        street_name = self._id_to_full.get(street_id, option)
        house_number = str(self.entry.data.get(CONF_HOUSE_NUMBER, "") or "").strip()

        try:
            group = await fetch_building_group(
                self.hass,
                int(self.entry.data.get(CONF_CITY_ID, DEFAULT_TERNOPIL_CITY_ID)),
                street_id,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Street change group autodetect failed: %s", err)
            group = ""

        data = dict(self.entry.data)
        data.update(
            {
                CONF_STREET_ID: street_id,
                CONF_STREET_NAME: street_name,
                CONF_GROUP: group,
            }
        )
        options = dict(self.entry.options)
        options[CONF_STREET_ID] = street_id

        title = street_name if not house_number else f"{street_name}, {house_number}"
        self.hass.config_entries.async_update_entry(self.entry, data=data, options=options, title=title)
        self._current_label = option
        self.async_write_ha_state()

        store = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        schedule = store.get(STORE_SCHEDULE_COORDINATOR) or store.get(STORE_LEGACY_SCHEDULE)
        ping = store.get(STORE_PING_COORDINATOR) or store.get(STORE_LEGACY_PING)
        if schedule:
            await schedule.async_request_refresh()
        if ping:
            await ping.async_request_refresh()


class TernopilOutageGroupSelect(_BaseTernopilSelect):
    _attr_icon = "mdi:home-group"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_outage_group"
        self._attr_name = f"{ENTITY_PREFIX} Outage Group"
        self._attr_options = []
        self._current_option: str | None = None

    async def _refresh_options(self) -> None:
        city_id = int(self.entry.data.get(CONF_CITY_ID, DEFAULT_TERNOPIL_CITY_ID))
        street_id = int(self.entry.options.get(CONF_STREET_ID, self.entry.data.get(CONF_STREET_ID, 0)) or 0)
        current_group = str(self.entry.data.get(CONF_GROUP, "") or "").strip()

        try:
            groups = await fetch_building_groups(self.hass, city_id, street_id) if street_id else []
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Outage group refresh failed: %s", err)
            groups = []

        options = [group for group in groups if group]
        if current_group and current_group not in options:
            options.append(current_group)
        if not options and current_group:
            options = [current_group]

        self._attr_options = sorted(set(options), key=str.casefold)
        self._current_option = current_group or (self._attr_options[0] if self._attr_options else None)

    @property
    def current_option(self) -> str | None:
        return str(self.entry.data.get(CONF_GROUP, "") or "").strip() or self._current_option

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            return
        if option == (self.current_option or ""):
            return

        data = dict(self.entry.data)
        data[CONF_GROUP] = option
        self.hass.config_entries.async_update_entry(self.entry, data=data)
        self._current_option = option
        self.async_write_ha_state()

        store = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        coordinator = store.get(STORE_SCHEDULE_COORDINATOR) or store.get(STORE_LEGACY_SCHEDULE)
        if coordinator:
            await coordinator.async_request_refresh()
