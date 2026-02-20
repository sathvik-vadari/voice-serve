"""Google Maps Places API – multi-strategy store discovery with deduplication."""
import logging
import time
from typing import Any

import aiohttp

from app.helpers.config import Config
from app.db.tickets import save_stores, log_tool_call

logger = logging.getLogger(__name__)

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"


async def find_stores(
    ticket_id: str,
    store_search_query: str,
    location: str,
    max_stores: int | None = None,
    search_queries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Search Google Maps using multiple strategies and merge results.

    If search_queries is provided, each query is tried in order and results
    are merged with deduplication by place_id.  Results from earlier queries
    (higher priority) are ranked above later ones.

    Falls back to a single search using store_search_query if search_queries
    is not given.
    """
    max_stores = max_stores or Config.MAX_STORES_TO_CALL
    api_key = Config.GOOGLE_MAPS_API_KEY
    if not api_key:
        raise ValueError("GOOGLE_MAPS_API_KEY is not set")

    queries = search_queries or [f"{store_search_query} near {location}"]
    queries_with_location = []
    for q in queries:
        low = q.lower()
        loc_low = location.lower()
        if loc_low not in low and "near" not in low:
            queries_with_location.append(f"{q} near {location}")
        else:
            queries_with_location.append(q)

    seen_place_ids: set[str] = set()
    all_places: list[tuple[int, dict]] = []

    async with aiohttp.ClientSession() as session:
        for priority, search_text in enumerate(queries_with_location):
            start = time.time()
            params = {"query": search_text, "key": api_key}

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

            store = {
                "name": detail.get("name") or place.get("name", "Unknown"),
                "address": detail.get("formatted_address") or place.get("formatted_address"),
                "phone_number": phone,
                "rating": detail.get("rating") or place.get("rating"),
                "total_ratings": detail.get("user_ratings_total") or place.get("user_ratings_total"),
                "place_id": place_id,
                "latitude": geo.get("lat"),
                "longitude": geo.get("lng"),
                "is_open_now": hours.get("open_now"),
                "business_status": detail.get("business_status"),
                "place_types": detail.get("types", []),
            }
            stores.append(store)

            log_tool_call(
                ticket_id, "google_maps_place_details",
                {"place_id": place_id},
                {"name": store["name"], "phone": phone, "open_now": store["is_open_now"]},
                latency_ms=int((time.time() - detail_start) * 1000),
            )

            if len([s for s in stores if s.get("phone_number")]) >= max_stores:
                break

    callable_stores = [s for s in stores if s.get("phone_number")]
    if not callable_stores:
        logger.warning("No stores found with phone numbers for ticket %s", ticket_id)

    save_stores(ticket_id, callable_stores)
    return callable_stores
