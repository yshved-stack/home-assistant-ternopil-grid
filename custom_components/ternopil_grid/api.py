"""HTTP client for the Ternopil PowerOn API (ternopil_grid integration).

Endpoints (observed):
 - Streets (Hydra JSON-LD):
     /api/pw_streets?pagination=false&city.id=1032[&name=...]
 - Building groups for a street:
     /api/pw-accounts/building-groups?cityId=1032&streetId=...
 - Actual graph (schedule) (Hydra JSON-LD collection):
     /api/a_gpv_g?after=...&before=...&group[]=4.1&time=<CITY><STREET>
   Requires:
     - Origin / Referer headers
     - x-debug-key = base64("<CITY>/<STREET>")
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aiohttp import ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

try:
    from yarl import URL
except Exception:  # pragma: no cover
    URL = None  # type: ignore

_LOGGER = logging.getLogger(__name__)

BASE = "https://api-poweron.toe.com.ua"
API = f"{BASE}/api"
ORIGIN = "https://poweron.toe.com.ua"
REFERER = "https://poweron.toe.com.ua/"
API_CACHE_KEY = "api_cache"
STREETS_CACHE_TTL_S = 12 * 3600
GROUPS_CACHE_TTL_S = 6 * 3600


def _debug_key(city_id: int | str, street_id: int | str) -> str:
    raw = f"{city_id}/{street_id}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _build_url(path: str, params: dict[str, str]) -> str:
    base = f"{API}/{path.lstrip('/')}"
    if URL is not None:
        return str(URL(base).with_query(params))
    # fallback (should be rare)
    from urllib.parse import urlencode

    return base + "?" + urlencode(params)


async def _get_json(hass, url: str, *, accept: str, headers: dict[str, str] | None = None) -> Any:
    session = async_get_clientsession(hass)

    hdrs = {
        "Accept": accept,
        "Origin": ORIGIN,
        "Referer": REFERER,
    }
    if headers:
        hdrs.update(headers)

    timeout = ClientTimeout(total=10)

    async with session.get(url, headers=hdrs, allow_redirects=False, timeout=timeout) as resp:
        text = await resp.text()

        if resp.status == 404:
            raise RuntimeError(f"HTTP 404: {url}")
        if resp.status >= 400:
            raise RuntimeError(f"Upstream HTTP {resp.status}: {text[:200]}")

        try:
            return await resp.json(content_type=None)
        except Exception as err:
            raise RuntimeError(f"Upstream non-JSON response: {text[:200]}") from err


def _cache_bucket(hass) -> dict[str, Any]:
    domain_bucket = hass.data.setdefault(DOMAIN, {})
    return domain_bucket.setdefault(API_CACHE_KEY, {})


def _cache_get(hass, key: str, ttl_s: int) -> Any:
    bucket = _cache_bucket(hass)
    record = bucket.get(key)
    if not isinstance(record, dict):
        return None
    cached_at = float(record.get("cached_at", 0))
    if datetime.now(timezone.utc).timestamp() - cached_at > ttl_s:
        return None
    return record.get("value")


def _cache_put(hass, key: str, value: Any) -> Any:
    bucket = _cache_bucket(hass)
    bucket[key] = {
        "cached_at": datetime.now(timezone.utc).timestamp(),
        "value": value,
    }
    return value


async def fetch_streets(hass, city_id: int, name_query: str | None = None) -> list[dict[str, Any]]:
    cache_key = None
    if not name_query:
        cache_key = f"streets:{city_id}"
        cached = _cache_get(hass, cache_key, STREETS_CACHE_TTL_S)
        if isinstance(cached, list):
            return cached

    params: dict[str, str] = {"pagination": "false", "city.id": str(city_id)}
    if name_query:
        params["name"] = name_query

    url = _build_url("pw_streets", params)
    data = await _get_json(hass, url, accept="application/ld+json")

    members = data.get("hydra:member") or []
    out: list[dict[str, Any]] = []
    for s in members:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        name = s.get("name")
        if isinstance(sid, int) and isinstance(name, str) and name.strip():
            out.append({"id": sid, "name": name.strip()})
    return _cache_put(hass, cache_key, out) if cache_key else out


async def fetch_building_groups(hass, city_id: int, street_id: int) -> list[str]:
    """Return list of building group codes for a given street.

    Non-fatal: returns [] on any upstream error.
    """
    cache_key = f"groups:{city_id}:{street_id}"
    cached = _cache_get(hass, cache_key, GROUPS_CACHE_TTL_S)
    if isinstance(cached, list):
        return cached

    url = _build_url(
        "pw-accounts/building-groups",
        {"cityId": str(city_id), "streetId": str(street_id)},
    )

    try:
        data = await _get_json(hass, url, accept="application/ld+json")
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("fetch_building_groups failed: %s", err)
        return []

    members = data.get("hydra:member") or data.get("buildingGroups") or []
    groups: list[str] = []
    for item in members:
        if isinstance(item, dict):
            grp = item.get("chergGpv") or item.get("group") or item.get("code")
            if isinstance(grp, str) and grp.strip():
                groups.append(grp.strip())
        elif isinstance(item, str) and item.strip():
            groups.append(item.strip())
    return _cache_put(hass, cache_key, groups)


async def fetch_building_group(hass, city_id: int, street_id: int) -> str:
    groups = await fetch_building_groups(hass, city_id, street_id)
    return groups[0] if groups else ""


def _utc_day_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date_graph(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def fetch_schedule(hass, *, city_id: int, street_id: int, group: str) -> dict[str, Any]:
    """Fetch schedule window day0..day2 (UTC) and return {"days":[(dateGraph,times_dict)], "raw":..., "empty":bool}.

    Non-fatal empty: returns empty=True when upstream returns no usable times.
    Retries on 5xx and empty payloads (common).
    """
    session = async_get_clientsession(hass)

    now = datetime.now(timezone.utc)
    day0 = _utc_day_start(now)
    day2 = day0 + timedelta(days=2)

    params = {
        "after": _format_utc(day0),
        "before": _format_utc(day2),
        "group[]": group,
        "time": f"{city_id}{street_id}",
    }
    url = _build_url("a_gpv_g", params)
    headers = {"x-debug-key": _debug_key(city_id, street_id)}

    last_empty: dict[str, Any] | None = None
    last_err: str | None = None

    for attempt in range(5):
        try:
            data = await _get_json(
                hass,
                url,
                accept="application/ld+json",
                headers=headers,
            )
        except Exception as err:  # noqa: BLE001
            last_err = str(err)
            await asyncio.sleep(1 * (2**attempt))
            continue

        members = data.get("hydra:member") or []
        if not members:
            last_empty = {"raw": data, "days": [], "empty": True}
            await asyncio.sleep(1 * (2**attempt))
            continue

        days: list[tuple[datetime, dict[str, Any]]] = []
        found_times = False

        for item in members:
            if not isinstance(item, dict):
                continue
            dg = _parse_date_graph(item.get("dateGraph")) or day0
            data_json = item.get("dataJson")
            times: dict[str, Any] = {}

            if isinstance(data_json, dict):
                grp_data = data_json.get(group)
                if isinstance(grp_data, dict):
                    t = grp_data.get("times")
                    if isinstance(t, dict):
                        times = t
                        if t:
                            found_times = True

            days.append((dg, times))

        if not found_times:
            return {"raw": data, "days": days, "empty": True}

        return {"raw": data, "days": days, "empty": False}

    if last_empty is not None:
        return last_empty
    raise RuntimeError(last_err or "schedule fetch failed")
