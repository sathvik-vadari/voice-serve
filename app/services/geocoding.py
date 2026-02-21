"""Google Maps Geocoding – forward and reverse geocode for addresses."""
import re
import logging
from typing import Any, Optional

import aiohttp

from app.helpers.config import Config

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

_INDIA_PINCODE_RE = re.compile(r"\b[1-9]\d{5}\b")


def extract_pincode(address: str) -> Optional[str]:
    """Try to extract a 6-digit Indian pincode from address text."""
    match = _INDIA_PINCODE_RE.search(address or "")
    return match.group(0) if match else None


def _parse_address_components(components: list[dict]) -> dict[str, str]:
    """Extract structured fields from Google geocoding address_components."""
    result: dict[str, str] = {}
    for comp in components:
        types = comp.get("types", [])
        if "postal_code" in types:
            result["pincode"] = comp["long_name"]
        elif "locality" in types:
            result["city"] = comp["long_name"]
        elif "administrative_area_level_1" in types:
            result["state"] = comp["long_name"]
        elif "sublocality_level_1" in types:
            result.setdefault("area", comp["long_name"])
        elif "route" in types:
            result.setdefault("street", comp["long_name"])
        elif "street_number" in types:
            result.setdefault("street_number", comp["long_name"])
    return result


async def geocode_address(address: str) -> Optional[dict[str, Any]]:
    """
    Forward-geocode an address string.
    Returns { lat, lng, pincode, city, state, formatted_address, area } or None.
    """
    if not Config.GOOGLE_MAPS_API_KEY:
        logger.error("GOOGLE_MAPS_API_KEY not set – cannot geocode")
        return None

    params = {
        "address": address,
        "key": Config.GOOGLE_MAPS_API_KEY,
        "region": "in",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(GEOCODE_URL, params=params) as resp:
                data = await resp.json()
    except Exception:
        logger.exception("Geocoding request failed for %r", address)
        return None

    results = data.get("results", [])
    if not results:
        logger.warning("Geocoding returned no results for %r (status=%s)", address, data.get("status"))
        return None

    top = results[0]
    loc = top.get("geometry", {}).get("location", {})
    parsed = _parse_address_components(top.get("address_components", []))

    return {
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "pincode": parsed.get("pincode") or extract_pincode(top.get("formatted_address", "")),
        "city": parsed.get("city"),
        "state": parsed.get("state"),
        "area": parsed.get("area"),
        "formatted_address": top.get("formatted_address"),
    }


async def reverse_geocode(lat: float, lng: float) -> Optional[dict[str, Any]]:
    """
    Reverse-geocode lat/lng to get pincode, city, state, formatted_address.
    """
    if not Config.GOOGLE_MAPS_API_KEY:
        logger.error("GOOGLE_MAPS_API_KEY not set – cannot reverse geocode")
        return None

    params = {
        "latlng": f"{lat},{lng}",
        "key": Config.GOOGLE_MAPS_API_KEY,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(GEOCODE_URL, params=params) as resp:
                data = await resp.json()
    except Exception:
        logger.exception("Reverse geocoding failed for (%s, %s)", lat, lng)
        return None

    results = data.get("results", [])
    if not results:
        logger.warning("Reverse geocoding returned no results for (%s, %s)", lat, lng)
        return None

    top = results[0]
    parsed = _parse_address_components(top.get("address_components", []))

    return {
        "pincode": parsed.get("pincode"),
        "city": parsed.get("city"),
        "state": parsed.get("state"),
        "area": parsed.get("area"),
        "formatted_address": top.get("formatted_address"),
    }
