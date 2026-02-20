"""Google Maps Places API – find nearby stores sorted by rating."""
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
) -> list[dict[str, Any]]:
    """
    Search Google Maps for stores matching the query near the location.
    Returns up to max_stores stores sorted by rating (descending).
    Each store dict includes: name, address, phone_number, rating, total_ratings, place_id, lat, lng.
    """
    max_stores = max_stores or Config.MAX_STORES_TO_CALL
    api_key = Config.GOOGLE_MAPS_API_KEY
    if not api_key:
        raise ValueError("GOOGLE_MAPS_API_KEY is not set")

    search_text = f"{store_search_query} near {location}"
    start = time.time()

    async with aiohttp.ClientSession() as session:
        # Step 1: Text Search
        params = {"query": search_text, "key": api_key}
        async with session.get(TEXT_SEARCH_URL, params=params) as resp:
            data = await resp.json()

        if data.get("status") != "OK":
            logger.error("Google Maps text search failed: %s", data.get("status"))
            log_tool_call(
                ticket_id, "google_maps_text_search",
                {"query": search_text}, {"status": data.get("status"), "error": data.get("error_message")},
                status="error", error_message=data.get("error_message"),
                latency_ms=int((time.time() - start) * 1000),
            )
            return []

        results = data.get("results") or []
        # Sort by rating descending, break ties by total ratings
        results.sort(key=lambda r: (r.get("rating") or 0, r.get("user_ratings_total") or 0), reverse=True)
        top = results[:max_stores]

        log_tool_call(
            ticket_id, "google_maps_text_search",
            {"query": search_text}, {"total_found": len(results), "selected": len(top)},
            latency_ms=int((time.time() - start) * 1000),
        )

        # Step 2: Get phone numbers via Place Details
        stores: list[dict[str, Any]] = []
        for place in top:
            place_id = place.get("place_id")
            if not place_id:
                continue
            detail_start = time.time()
            detail_params = {
                "place_id": place_id,
                "fields": "formatted_phone_number,international_phone_number,name,rating,user_ratings_total,formatted_address,geometry",
                "key": api_key,
            }
            async with session.get(PLACE_DETAILS_URL, params=detail_params) as dresp:
                ddata = await dresp.json()

            detail = ddata.get("result") or {}
            phone = detail.get("international_phone_number") or detail.get("formatted_phone_number")
            geo = detail.get("geometry", {}).get("location", {})

            store = {
                "name": detail.get("name") or place.get("name", "Unknown"),
                "address": detail.get("formatted_address") or place.get("formatted_address"),
                "phone_number": phone,
                "rating": detail.get("rating") or place.get("rating"),
                "total_ratings": detail.get("user_ratings_total") or place.get("user_ratings_total"),
                "place_id": place_id,
                "latitude": geo.get("lat"),
                "longitude": geo.get("lng"),
            }
            stores.append(store)

            log_tool_call(
                ticket_id, "google_maps_place_details",
                {"place_id": place_id}, {"name": store["name"], "phone": phone},
                latency_ms=int((time.time() - detail_start) * 1000),
            )

    # Filter to only stores that have a phone number
    callable_stores = [s for s in stores if s.get("phone_number")]
    if not callable_stores:
        logger.warning("No stores found with phone numbers for ticket %s", ticket_id)

    save_stores(ticket_id, callable_stores)
    return callable_stores
