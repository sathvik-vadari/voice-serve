"""Google Maps Places API – multi-strategy store discovery with deduplication."""
import logging
import math
import time
from typing import Any

import aiohttp

from app.helpers.config import Config
from app.db.tickets import save_stores, log_tool_call
from app.services.geocoding import geocode_address

logger = logging.getLogger(__name__)

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"


def _has_location_overlap(query: str, location: str) -> bool:
    """Check if any significant part of the location already appears in the query."""
    location_parts = [p.strip().lower() for p in location.split(",")]
    query_low = query.lower()
    for part in location_parts:
        if len(part) >= 3 and part in query_low:
            return True
    return False


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _extract_city_area(location: str) -> str:
    """Extract neighborhood + city from a full address for cleaner search queries.

    '1st Floor, faffHQ, HSR Layout, Bangalore' → 'HSR Layout, Bangalore'
    """
    parts = [p.strip() for p in location.split(",")]
    skip_keywords = ["floor", "flat", "door", "shop", "no.", "no ", "building", "#"]
    meaningful = [
        p for p in parts
        if len(p.strip()) >= 3
        and not any(kw in p.lower() for kw in skip_keywords)
    ]
    if len(meaningful) >= 2:
        return ", ".join(meaningful[-2:])
    return meaningful[-1] if meaningful else location


async def find_stores(
    ticket_id: str,
    store_search_query: str,
    location: str,
    max_stores: int | None = None,
    search_queries: list[str] | None = None,
    specific_store_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search Google Maps using multiple strategies and merge results.

    If search_queries is provided, each query is tried in order and results
    are merged with deduplication by place_id.  Results from earlier queries
    (higher priority) are ranked above later ones.

    When specific_store_name is set, a bare store-name search (no location)
    is prepended as the highest-priority query so the exact store is found
    even if it's outside the immediate area.

    Falls back to a single search using store_search_query if search_queries
    is not given.
    """
    max_stores = max_stores or Config.MAX_STORES_TO_CALL
    api_key = Config.GOOGLE_MAPS_API_KEY
    if not api_key:
        raise ValueError("GOOGLE_MAPS_API_KEY is not set")

    city_area = _extract_city_area(location)

    # Geocode user location for proximity bias and distance sorting
    user_lat: float | None = None
    user_lng: float | None = None
    try:
        user_geo = await geocode_address(location)
        if user_geo:
            user_lat = user_geo.get("lat")
            user_lng = user_geo.get("lng")
            logger.info("Ticket %s: user location geocoded to (%s, %s)", ticket_id, user_lat, user_lng)
    except Exception:
        logger.warning("Ticket %s: failed to geocode user location, skipping proximity bias", ticket_id)

    queries = search_queries or [f"{store_search_query} near {location}"]

    if specific_store_name:
        bare = specific_store_name.strip()
        city_query = f"{bare} {city_area}"
        prepend = []
        if bare.lower() not in [q.lower().strip() for q in queries]:
            prepend.append(bare)
        if city_query.lower() not in [q.lower().strip() for q in queries]:
            prepend.append(city_query)
        queries = prepend + queries

    queries_with_location = []
    for q in queries:
        low = q.lower()
        if _has_location_overlap(q, location) or "near" in low:
            queries_with_location.append(q)
        else:
            queries_with_location.append(f"{q} near {city_area}")

    seen_place_ids: set[str] = set()
    all_places: list[tuple[int, dict]] = []

    async with aiohttp.ClientSession() as session:
        for priority, search_text in enumerate(queries_with_location):
            start = time.time()
            params: dict[str, Any] = {"query": search_text, "key": api_key}

            if user_lat and user_lng:
                params["location"] = f"{user_lat},{user_lng}"
                params["radius"] = "50000"

            async with session.get(TEXT_SEARCH_URL, params=params) as resp:
                data = await resp.json()

            if data.get("status") != "OK":
                logger.warning(
                    "Google Maps search failed for %r: %s", search_text, data.get("status"),
                )
                log_tool_call(
                    ticket_id, "google_maps_text_search",
                    {"query": search_text, "strategy_priority": priority},
                    {"status": data.get("status"), "error": data.get("error_message")},
                    status="error", error_message=data.get("error_message"),
                    latency_ms=int((time.time() - start) * 1000),
                )
                continue

            results = data.get("results") or []
            results.sort(
                key=lambda r: (r.get("rating") or 0, r.get("user_ratings_total") or 0),
                reverse=True,
            )

            new_count = 0
            for place in results:
                pid = place.get("place_id")
                if not pid or pid in seen_place_ids:
                    continue
                seen_place_ids.add(pid)
                all_places.append((priority, place))
                new_count += 1

            log_tool_call(
                ticket_id, "google_maps_text_search",
                {"query": search_text, "strategy_priority": priority},
                {"total_found": len(results), "new_unique": new_count},
                latency_ms=int((time.time() - start) * 1000),
            )

        all_places.sort(key=lambda x: (
            x[0],
            -(x[1].get("rating") or 0),
            -(x[1].get("user_ratings_total") or 0),
        ))
        top = all_places[:max_stores * 2]

        stores: list[dict[str, Any]] = []
        for _priority, place in top:
            place_id = place.get("place_id")
            if not place_id:
                continue
            detail_start = time.time()
            detail_params = {
                "place_id": place_id,
                "fields": (
                    "formatted_phone_number,international_phone_number,"
                    "name,rating,user_ratings_total,formatted_address,"
                    "geometry,opening_hours,business_status,types"
                ),
                "key": api_key,
            }
            async with session.get(PLACE_DETAILS_URL, params=detail_params) as dresp:
                ddata = await dresp.json()

            detail = ddata.get("result") or {}
            phone = detail.get("international_phone_number") or detail.get("formatted_phone_number")
            geo = detail.get("geometry", {}).get("location", {})
            hours = detail.get("opening_hours", {})

            store_lat = geo.get("lat")
            store_lng = geo.get("lng")
            distance_km: float | None = None
            if user_lat and user_lng and store_lat and store_lng:
                distance_km = round(_haversine_km(user_lat, user_lng, store_lat, store_lng), 2)

            store = {
                "name": detail.get("name") or place.get("name", "Unknown"),
                "address": detail.get("formatted_address") or place.get("formatted_address"),
                "phone_number": phone,
                "rating": detail.get("rating") or place.get("rating"),
                "total_ratings": detail.get("user_ratings_total") or place.get("user_ratings_total"),
                "place_id": place_id,
                "latitude": store_lat,
                "longitude": store_lng,
                "is_open_now": hours.get("open_now"),
                "business_status": detail.get("business_status"),
                "place_types": detail.get("types", []),
                "distance_km": distance_km,
            }
            stores.append(store)

            log_tool_call(
                ticket_id, "google_maps_place_details",
                {"place_id": place_id},
                {"name": store["name"], "phone": phone, "open_now": store["is_open_now"],
                 "distance_km": distance_km},
                latency_ms=int((time.time() - detail_start) * 1000),
            )

            if len([s for s in stores if s.get("phone_number")]) >= max_stores:
                break

    callable_stores = [
        s for s in stores
        if s.get("phone_number") and s.get("is_open_now") is not False
    ]
    closed_count = sum(
        1 for s in stores
        if s.get("phone_number") and s.get("is_open_now") is False
    )
    if closed_count:
        logger.info(
            "Ticket %s: skipped %d store(s) that Google Maps reports as currently closed",
            ticket_id, closed_count,
        )

    if specific_store_name and callable_stores:
        callable_stores.sort(key=lambda s: s.get("distance_km") or 9999)
        logger.info(
            "Ticket %s: sorted %d stores by distance for specific store '%s'",
            ticket_id, len(callable_stores), specific_store_name,
        )

    if not callable_stores:
        logger.warning("No stores found with phone numbers for ticket %s", ticket_id)

    save_stores(ticket_id, callable_stores)
    return callable_stores
