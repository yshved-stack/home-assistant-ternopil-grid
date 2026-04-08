from __future__ import annotations

import asyncio
from ipaddress import ip_address
import logging
from datetime import datetime, timedelta, time as dtime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import fetch_building_group, fetch_schedule
from .const import (
    CONF_CITY_ID,
    CONF_DEBUG_LOGGING,
    CONF_GROUP,
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
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_PING_HISTORY_HOURS,
)
from .ping import ping

_LOGGER = logging.getLogger(__name__)

SLOT_SECONDS = 1800  # 30 minutes
SLOTS_PER_DAY = 48


def _val_to_color(v: str | None) -> str:
    # upstream: "0"=power on, "1"=outage, "10"=limited/uncertain
    if v == "0":
        return "green"
    if v == "1":
        return "red"
    if v == "10":
        return "yellow"
    return "unknown"


def _local_midnight_from_dategraph(dg_utc: datetime) -> datetime:
    """Local midnight for local date of dg_utc."""
    tz = dt_util.DEFAULT_TIME_ZONE
    local_date = dt_util.as_local(dg_utc).date()
    return datetime.combine(local_date, dtime.min).replace(tzinfo=tz)


def _build_day_bins(day0_local: datetime, times: dict[str, Any]) -> list[tuple[float, float, str]]:
    """Build 48 bins for a local day; return bins in UTC timestamps."""
    bins: list[tuple[float, float, str]] = []
    for i in range(SLOTS_PER_DAY):
        t0_local = day0_local + timedelta(seconds=SLOT_SECONDS * i)
        t1_local = t0_local + timedelta(seconds=SLOT_SECONDS)
        key = t0_local.strftime("%H:%M")
        v = times.get(key)
        color = _val_to_color(v if isinstance(v, str) else None)
        bins.append((dt_util.as_utc(t0_local).timestamp(), dt_util.as_utc(t1_local).timestamp(), color))
    return bins


def _merge_bins(bins: list[tuple[float, float, str]]) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    for s, e, c in bins:
        if not segs:
            segs.append({"start_ts": s, "end_ts": e, "color": c})
            continue
        last = segs[-1]
        if last["color"] == c and abs(float(last["end_ts"]) - s) < 1e-6:
            last["end_ts"] = e
        else:
            segs.append({"start_ts": s, "end_ts": e, "color": c})
    return segs


def _looks_like_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _resolve_target_name(hass: HomeAssistant, entity_id: str) -> str | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return entity_id
    friendly_name = str(state.attributes.get("friendly_name") or "").strip()
    resolved = str(getattr(state, "name", "") or friendly_name or entity_id).strip()
    return resolved or entity_id


def _resolve_target_ip(hass: HomeAssistant, entity_id: str) -> str | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return None

    for key in ("ip", "ip_address", "address", "host", "local_ip"):
        candidate = str(state.attributes.get(key) or "").strip()
        if candidate and _looks_like_ip(candidate):
            return candidate

    state_value = str(state.state or "").strip()
    return state_value if _looks_like_ip(state_value) else None


def _format_target_display(
    *,
    target_name: str | None,
    target_entity_id: str,
    ping_method: str,
    ping_ip: str,
    ping_port: int,
    http_ssl: bool,
    http_path: str,
) -> str:
    subject = target_name or target_entity_id or "Manual target"
    method = (ping_method or "icmp").lower()

    if method == "entity":
        return f"{subject} · entity status"
    if method == "http":
        scheme = "HTTPS" if http_ssl else "HTTP"
        port = ping_port or (443 if http_ssl else 80)
        return f"{subject} · {scheme} {ping_ip}:{port}{http_path}"
    if method == "tcp":
        return f"{subject} · TCP {ping_ip}:{ping_port or DEFAULT_PING_PORT}"
    if ping_ip:
        return f"{subject} · ICMP {ping_ip}"
    return subject


class TernopilScheduleCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Fetch schedule and provide merged color segments (UTC timestamps)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self.city_id = int(entry.data.get(CONF_CITY_ID, DEFAULT_TERNOPIL_CITY_ID))
        self.street_id = int(entry.options.get(CONF_STREET_ID, entry.data.get(CONF_STREET_ID, 0)) or 0)
        self.group = str(entry.data.get(CONF_GROUP, "") or "")

        self._last_segs: list[dict[str, Any]] = []
        self._tg_last_success_at: datetime | None = None
        self._tg_last_failure_at: datetime | None = None
        self._tg_last_error: str = ""
        self._tg_refresh_count = 0
        self._tg_empty_count = 0

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_schedule",
            update_interval=timedelta(seconds=int(DEFAULT_UPDATE_INTERVAL)),
        )

    async def _async_update_data(self) -> list[dict[str, Any]]:
        def mark_failure(message: str, *, empty: bool = False) -> None:
            self._tg_last_failure_at = dt_util.utcnow()
            self._tg_last_error = message
            if empty:
                self._tg_empty_count += 1

        def mark_success() -> None:
            self._tg_last_success_at = dt_util.utcnow()
            self._tg_last_error = ""
            self._tg_refresh_count += 1

        # allow street/group changes via config entry state
        street_id = int(self.entry.options.get(CONF_STREET_ID, self.street_id) or 0)
        if not street_id:
            return self._last_segs

        # street changed -> force group re-detect
        if street_id != self.street_id:
            self.street_id = street_id
            self.group = ""
            data = dict(self.entry.data)
            data[CONF_STREET_ID] = street_id
            data[CONF_GROUP] = ""  # clear
            self.hass.config_entries.async_update_entry(self.entry, data=data)

        # auto-detect group when missing
        if not self.group:
            grp = await fetch_building_group(self.hass, self.city_id, self.street_id)
            if grp:
                self.group = grp
                data = dict(self.entry.data)
                data[CONF_GROUP] = grp
                self.hass.config_entries.async_update_entry(self.entry, data=data)
            else:
                mark_failure(f"group autodetect failed for street_id={self.street_id}")
                _LOGGER.warning("Group autodetect failed for street_id=%s; keeping last data", self.street_id)
                return self._last_segs

        try:
            result = await fetch_schedule(
                self.hass,
                city_id=self.city_id,
                street_id=self.street_id,
                group=self.group,
            )
        except Exception as err:  # noqa: BLE001
            mark_failure(str(err))
            _LOGGER.warning("Schedule fetch failed: %s; keeping last data", err)
            return self._last_segs

        days = result.get("days") or []
        if result.get("empty") or not days:
            mark_failure("schedule empty", empty=True)
            _LOGGER.warning("Schedule empty; keeping last data")
            return self._last_segs

        all_bins: list[tuple[float, float, str]] = []
        for dg, times in days:
            if not isinstance(dg, datetime):
                continue
            if not isinstance(times, dict):
                times = {}
            day0_local = _local_midnight_from_dategraph(dg)
            all_bins.extend(_build_day_bins(day0_local, times))

        if not all_bins:
            mark_failure("schedule bins empty", empty=True)
            return self._last_segs

        all_bins.sort(key=lambda x: x[0])
        self._last_segs = _merge_bins(all_bins)
        mark_success()
        if bool(self.entry.options.get(CONF_DEBUG_LOGGING, False)):
            _LOGGER.debug(
                "Schedule refresh ok: street_id=%s group=%s segments=%s refreshes=%s",
                self.street_id,
                self.group,
                len(self._last_segs),
                self._tg_refresh_count,
            )
        return self._last_segs


class TernopilPingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Ping coordinator + in-memory 30m history."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        ping_ip: str | None = None,
        ping_interval: int | None = None,
        ping_method: str | None = None,
        ping_port: int | None = None,
        ping_timeout: float | None = None,
        history_hours: int | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry

        opts = entry.options
        self.ping_ip = str(ping_ip if ping_ip is not None else opts.get(CONF_PING_IP, DEFAULT_PING_IP))
        self.ping_method = str(ping_method if ping_method is not None else opts.get(CONF_PING_METHOD, DEFAULT_PING_METHOD)).lower()
        self.ping_port = int(ping_port if ping_port is not None else opts.get(CONF_PING_PORT, DEFAULT_PING_PORT) or 0)
        self.ping_timeout = float(ping_timeout if ping_timeout is not None else opts.get(CONF_PING_TIMEOUT, DEFAULT_PING_TIMEOUT) or DEFAULT_PING_TIMEOUT)
        self._interval = int(ping_interval if ping_interval is not None else opts.get(CONF_PING_INTERVAL, DEFAULT_PING_INTERVAL) or DEFAULT_PING_INTERVAL)

        self._slot_seconds = SLOT_SECONDS
        self._history_hours = max(
            1,
            min(
                MAX_PING_HISTORY_HOURS,
                int(history_hours if history_hours is not None else opts.get(CONF_PING_HISTORY_HOURS, DEFAULT_PING_HISTORY_HOURS) or DEFAULT_PING_HISTORY_HOURS),
            ),
        )
        self._history_slots = max(1, min(SLOTS_PER_DAY, int((self._history_hours * 3600) // self._slot_seconds)))
        self._tg_last_success_at: datetime | None = None
        self._tg_last_failure_at: datetime | None = None
        self._tg_last_error: str = ""
        self._tg_success_count = 0
        self._tg_failure_count = 0
        self._tg_consecutive_failures = 0

        from collections import deque
        self._history: deque[tuple[int, bool]] = deque(maxlen=self._history_slots)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_ping",
            update_interval=timedelta(seconds=self._interval),
        )

    def _bucket_ts(self, ts: float) -> int:
        return int(ts // self._slot_seconds) * self._slot_seconds

    def _push_history(self, bucket_ts: int, ok: bool) -> None:
        if self._history and self._history[-1][0] == bucket_ts:
            self._history[-1] = (bucket_ts, ok)
        else:
            self._history.append((bucket_ts, ok))

    def history_slots(self) -> list[dict[str, Any]]:
        return [{"ts": ts, "start_ts": ts, "end_ts": ts + self._slot_seconds, "ok": ok} for ts, ok in self._history]

    async def _async_update_data(self) -> dict[str, Any]:
        def mark_success() -> None:
            self._tg_last_success_at = dt_util.utcnow()
            self._tg_last_error = ""
            self._tg_success_count += 1
            self._tg_consecutive_failures = 0

        def mark_failure(message: str, *, count_failure: bool) -> None:
            self._tg_last_failure_at = dt_util.utcnow()
            self._tg_last_error = message
            if count_failure:
                self._tg_failure_count += 1
                self._tg_consecutive_failures += 1

        opts = self.entry.options

        self.ping_method = str(opts.get(CONF_PING_METHOD, DEFAULT_PING_METHOD)).lower().strip()
        self.ping_ip = str(opts.get(CONF_PING_IP, DEFAULT_PING_IP) or "").strip()
        self.ping_port = int(opts.get(CONF_PING_PORT, DEFAULT_PING_PORT) or 0)
        self.ping_timeout = float(opts.get(CONF_PING_TIMEOUT, DEFAULT_PING_TIMEOUT) or DEFAULT_PING_TIMEOUT)

        self._interval = int(opts.get(CONF_PING_INTERVAL, DEFAULT_PING_INTERVAL) or DEFAULT_PING_INTERVAL)
        self.update_interval = timedelta(seconds=max(1, self._interval))

        self._history_hours = max(
            1,
            min(
                MAX_PING_HISTORY_HOURS,
                int(opts.get(CONF_PING_HISTORY_HOURS, DEFAULT_PING_HISTORY_HOURS) or DEFAULT_PING_HISTORY_HOURS),
            ),
        )
        self._history_slots = max(1, min(SLOTS_PER_DAY, int((self._history_hours * 3600) // self._slot_seconds)))

        # keep maxlen in sync
        if getattr(self._history, "maxlen", None) != self._history_slots:
            from collections import deque
            self._history = deque(list(self._history)[-self._history_slots :], maxlen=self._history_slots)

        enabled = bool(opts.get(CONF_PING_ENABLED, DEFAULT_PING_ENABLED))
        target_entity_id = str(opts.get(CONF_PING_ENTITY_ID, "") or "").strip()
        target_name = _resolve_target_name(self.hass, target_entity_id)
        effective_ip = self.ping_ip or _resolve_target_ip(self.hass, target_entity_id) or ""
        http_ssl = bool(opts.get(CONF_PING_HTTP_SSL, DEFAULT_PING_HTTP_SSL))
        http_path = str(opts.get(CONF_PING_HTTP_PATH, DEFAULT_PING_HTTP_PATH) or "/").strip() or "/"
        if not http_path.startswith("/"):
            http_path = "/" + http_path
        cutoff_ts = dt_util.utcnow().timestamp() - (self._history_hours * 3600)
        base = {
            "ip": effective_ip,
            "configured_ip": self.ping_ip,
            "port": self.ping_port,
            "method": self.ping_method,
            "timeout": self.ping_timeout,
            "target_entity_id": target_entity_id,
            "target_name": target_name,
            "target_display": _format_target_display(
                target_name=target_name,
                target_entity_id=target_entity_id,
                ping_method=self.ping_method,
                ping_ip=effective_ip,
                ping_port=self.ping_port,
                http_ssl=http_ssl,
                http_path=http_path,
            ),
            "history_hours": self._history_hours,
        }

        if not enabled:
            return {**base, "ok": None, "disabled": True, "cutoff_ts": cutoff_ts, "history_slots": self.history_slots()}

        if not effective_ip and self.ping_method != "entity":
            mark_failure("ping_ip not set", count_failure=False)
            return {**base, "ok": None, "disabled": False, "cutoff_ts": cutoff_ts, "error": "ping_ip not set", "history_slots": self.history_slots()}

        ok: bool | None = None

        # small retry loop to reduce flapping on transient errors
        for attempt in range(3):
            try:
                if self.ping_method == "entity":
                    ent_id = str(opts.get(CONF_PING_ENTITY_ID, "") or "").strip()
                    if not ent_id:
                        mark_failure("ping_entity_id not set", count_failure=False)
                        return {**base, "ok": None, "disabled": False, "cutoff_ts": cutoff_ts, "error": "ping_entity_id not set", "history_slots": self.history_slots()}
                    st = self.hass.states.get(ent_id)
                    ok = st is not None and st.state not in ("unknown", "unavailable", "")
                elif self.ping_method == "http":
                    import aiohttp

                    scheme = "https" if http_ssl else "http"
                    port = self.ping_port or (443 if http_ssl else 80)
                    url = f"{scheme}://{effective_ip}:{port}{http_path}"

                    session = async_get_clientsession(self.hass)
                    timeout = aiohttp.ClientTimeout(total=max(0.5, self.ping_timeout))
                    try:
                        async with session.head(url, timeout=timeout, allow_redirects=True) as resp:
                            ok = 200 <= int(resp.status) < 500
                    except Exception:
                        async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
                            ok = 200 <= int(resp.status) < 500
                else:
                    port = self.ping_port or (DEFAULT_PING_PORT if self.ping_method == "tcp" else 0)
                    ok = await ping(effective_ip, timeout_s=self.ping_timeout, method=self.ping_method, port=port)

                break
            except Exception as err:  # noqa: BLE001
                if attempt < 2:
                    await asyncio.sleep(1 * (2**attempt))
                    continue
                mark_failure(str(err), count_failure=True)
                raise UpdateFailed(str(err)) from err

        now_ts = dt_util.utcnow().timestamp()
        self._push_history(self._bucket_ts(now_ts), bool(ok))
        if bool(ok):
            mark_success()
        else:
            mark_failure("probe returned false", count_failure=True)
        if bool(self.entry.options.get(CONF_DEBUG_LOGGING, False)):
            _LOGGER.debug(
                "Probe refresh: method=%s ip=%s entity=%s ok=%s consecutive_failures=%s successes=%s failures=%s",
                self.ping_method,
                effective_ip,
                target_entity_id,
                bool(ok),
                self._tg_consecutive_failures,
                self._tg_success_count,
                self._tg_failure_count,
            )
        return {**base, "ok": bool(ok), "disabled": False, "cutoff_ts": cutoff_ts, "history_slots": self.history_slots()}
