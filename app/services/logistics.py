"""ProRouting Logistics API client – quotes, order creation, and order placement orchestration."""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import aiohttp

from app.helpers.config import Config
from app.db.tickets import (
    get_ticket,
    get_product,
    get_store_calls_for_ticket,
    get_store_by_id,
    update_ticket_status,
    create_logistics_order,
    update_logistics_order_placed,
    update_logistics_order_error,
    get_logistics_order,
    get_failed_lsp_ids,
)
from app.services.geocoding import geocode_address, reverse_geocode, extract_pincode

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-pro-api-key": Config.PROROUTING_API_KEY or "",
    }


def _base_url() -> str:
    return Config.PROROUTING_BASE_URL.rstrip("/")


# ---------------------------------------------------------------------------
# ProRouting API calls
# ---------------------------------------------------------------------------

async def get_delivery_quotes(
    pickup_lat: float,
    pickup_lng: float,
    pickup_pincode: str,
    drop_lat: float,
    drop_lng: float,
    drop_pincode: str,
    city: str,
    order_amount: float,
    order_weight: float = 1.0,
) -> dict[str, Any]:
    """
    Call ProRouting /partner/quotes to get available LSPs and their prices.
    Returns the full response dict with 'quotes', 'quote_id', 'valid_until'.
    """
    payload = {
        "pickup": {
            "lat": pickup_lat,
            "lng": pickup_lng,
            "pincode": pickup_pincode,
        },
        "drop": {
            "lat": drop_lat,
            "lng": drop_lng,
            "pincode": drop_pincode,
        },
        "city": city,
        "order_category": "F&B",
        "search_category": "Immediate Delivery",
        "order_amount": order_amount,
        "cod_amount": 0,
        "order_weight": order_weight,
    }

    url = f"{_base_url()}/partner/quotes"
    logger.info("ProRouting /quotes request: %s", payload)

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            data = await resp.json()

    logger.info("ProRouting /quotes response status=%s, quotes=%d",
                data.get("status"), len(data.get("quotes", [])))
    return data


def find_cheapest_quote(quotes_response: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Pick the cheapest quote by price_forward from a /quotes response."""
    quotes = quotes_response.get("quotes", [])
    if not quotes:
        return None
    return min(quotes, key=lambda q: float(q.get("price_forward", 999999)))


async def create_delivery_order(
    client_order_id: str,
    pickup: dict[str, Any],
    drop: dict[str, Any],
    callback_url: str,
    order_amount: float,
    order_weight: float,
    order_items: list[dict[str, Any]],
    selected_lsp_id: str,
    quote_id: Optional[str] = None,
    customer_promised_minutes: int = 60,
) -> dict[str, Any]:
    """
    Call ProRouting /partner/order/createasync to place a delivery order.
    Uses selected_lsp mode after picking cheapest from quotes.
    """
    promised_time = datetime.now(IST) + timedelta(minutes=customer_promised_minutes)

    payload = {
        "client_order_id": client_order_id,
        "retail_order_id": client_order_id,
        "pickup": pickup,
        "drop": drop,
        "customer_promised_time": promised_time.strftime("%Y-%m-%d %H:%M:%S"),
        "callback_url": callback_url,
        "order_category": "F&B",
        "search_category": "Immediate Delivery",
        "order_amount": order_amount,
        "cod_amount": 0,
        "order_weight": order_weight,
        "order_items": order_items,
        "order_ready": True,
        "select_criteria": {
            "mode": "selected_lsp",
            "lsp_id": selected_lsp_id,
        },
    }

    if quote_id:
        payload["select_criteria"]["quote_id"] = quote_id

    url = f"{_base_url()}/partner/order/createasync"
    logger.info("ProRouting /createasync request for %s (lsp=%s)", client_order_id, selected_lsp_id)

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            data = await resp.json()

    logger.info("ProRouting /createasync response: %s", data)
    return data


async def get_order_status(prorouting_order_id: str) -> dict[str, Any]:
    """Call ProRouting /partner/order/status to get current order state."""
    url = f"{_base_url()}/partner/order/status"
    payload = {"order_id": prorouting_order_id}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            return await resp.json()


async def get_order_tracking(prorouting_order_id: str) -> dict[str, Any]:
    """Call ProRouting /partner/order/track for rider location and tracking URL."""
    url = f"{_base_url()}/partner/order/track"
    payload = {"order_id": prorouting_order_id}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            return await resp.json()


# ---------------------------------------------------------------------------
# Orchestration: full order placement flow
# ---------------------------------------------------------------------------

def _build_options_for_confirm(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same sorting logic as options_summary._build_options to ensure consistent option indices."""
    options = []
    for c in calls:
        if c.get("status") != "analyzed" or not c.get("product_available"):
            continue
        analysis = c.get("call_analysis") or {}
        options.append({
            "store_call_id": c["id"],
            "store_id": c["store_id"],
            "store_name": c.get("store_name"),
            "address": c.get("address"),
            "phone_number": c.get("phone_number"),
            "matched_product": c.get("matched_product"),
            "price": c.get("price"),
            "product_match_type": c.get("product_match_type"),
            "specs_match_score": analysis.get("specs_match_score"),
        })

    match_weight = {"exact": 4, "close": 3, "alternative": 2, "no_match": 0, "no_data": 0}
    options.sort(
        key=lambda o: (
            -(match_weight.get(o.get("product_match_type") or "", 0) * 3
              + float(o.get("specs_match_score") or 0) * 2),
            o.get("price") or 999999,
        )
    )
    return options


async def place_order(
    ticket_id: str,
    *,
    store_call_id: Optional[int] = None,
    selected_option: Optional[int] = None,
    customer_name: Optional[str] = None,
) -> None:
    """
    Full order placement pipeline:
    1. Resolve selected store via store_call_id (preferred) or selected_option index
    2. Geocode customer location + get store pincode
    3. Get delivery quotes from ProRouting
    4. Pick cheapest LSP
    5. Create delivery order via ProRouting
    6. Update DB with order details
    """
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise ValueError(f"Ticket {ticket_id} not found")

    product = get_product(ticket_id)
    all_calls = get_store_calls_for_ticket(ticket_id)
    options = _build_options_for_confirm(all_calls)

    chosen = None

    if store_call_id:
        chosen = next((o for o in options if o["store_call_id"] == store_call_id), None)
        if not chosen:
            update_ticket_status(ticket_id, "failed", error_message=f"store_call_id {store_call_id} not found among available options")
            return
    elif selected_option:
        if selected_option < 1 or selected_option > len(options):
            update_ticket_status(ticket_id, "failed", error_message=f"Invalid option {selected_option}, only {len(options)} available")
            return
        chosen = options[selected_option - 1]
    else:
        update_ticket_status(ticket_id, "failed", error_message="No store_call_id or selected_option provided")
        return

    store = get_store_by_id(chosen["store_id"])
    if not store:
        update_ticket_status(ticket_id, "failed", error_message="Selected store not found in DB")
        return

    product_name = product["product_name"] if product else "Item"
    product_price = chosen.get("price") or (product.get("avg_price_online") if product else 0) or 0
    if product_price > 1000:
        product_price = 999

    # --- Geocode customer delivery location ---
    update_ticket_status(ticket_id, "placing_order")
    customer_location = ticket.get("location", "")
    customer_geo = await geocode_address(customer_location)
    if not customer_geo or not customer_geo.get("lat"):
        update_ticket_status(ticket_id, "failed", error_message=f"Could not geocode customer location: {customer_location}")
        return

    drop_lat = customer_geo["lat"]
    drop_lng = customer_geo["lng"]
    drop_pincode = customer_geo.get("pincode") or "000000"
    drop_city = customer_geo.get("city") or ""
    drop_state = customer_geo.get("state") or ""

    # --- Get store pickup pincode ---
    pickup_lat = store.get("latitude")
    pickup_lng = store.get("longitude")
    store_address = store.get("address") or ""
    pickup_pincode = extract_pincode(store_address)

    if not pickup_pincode and pickup_lat and pickup_lng:
        store_geo = await reverse_geocode(pickup_lat, pickup_lng)
        if store_geo:
            pickup_pincode = store_geo.get("pincode")

    pickup_pincode = pickup_pincode or "000000"
    city = drop_city or customer_location.split(",")[-1].strip()

    # --- Generate unique order ID ---
    short_uid = uuid.uuid4().hex[:8]
    client_order_id = f"{ticket_id}_{short_uid}"

    # --- Create logistics_order record in DB ---
    logistics_id = create_logistics_order(
        ticket_id=ticket_id,
        store_call_id=chosen["store_call_id"],
        client_order_id=client_order_id,
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        pickup_address=store_address,
        pickup_pincode=pickup_pincode,
        pickup_phone=store.get("phone_number"),
        drop_lat=drop_lat,
        drop_lng=drop_lng,
        drop_address=customer_geo.get("formatted_address", customer_location),
        drop_pincode=drop_pincode,
        drop_phone=ticket.get("user_phone"),
        customer_name=customer_name or ticket.get("user_phone"),
        order_amount=product_price,
        order_weight=1.0,
    )

    # --- Get delivery quotes ---
    try:
        quotes_resp = await get_delivery_quotes(
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            pickup_pincode=pickup_pincode,
            drop_lat=drop_lat,
            drop_lng=drop_lng,
            drop_pincode=drop_pincode,
            city=city,
            order_amount=product_price,
            order_weight=1.0,
        )
    except Exception as e:
        logger.exception("ProRouting /quotes failed for ticket %s", ticket_id)
        update_logistics_order_error(logistics_id, f"Quotes API failed: {e}")
        update_ticket_status(ticket_id, "failed", error_message=f"Delivery quotes failed: {e}")
        return

    if quotes_resp.get("status") != 1 or not quotes_resp.get("quotes"):
        msg = quotes_resp.get("message", "No delivery partners available for this route")
        update_logistics_order_error(logistics_id, msg)
        update_ticket_status(ticket_id, "failed", error_message=msg)
        return

    cheapest = find_cheapest_quote(quotes_resp)
    quote_id = quotes_resp.get("quote_id")
    logger.info(
        "Ticket %s: cheapest LSP = %s (₹%s, pickup ETA %s min)",
        ticket_id, cheapest.get("logistics_seller"),
        cheapest.get("price_forward"), cheapest.get("pickup_eta"),
    )

    # --- Build pickup/drop payloads for createasync ---
    store_name = store.get("store_name", "Store")
    pickup_payload = {
        "lat": pickup_lat,
        "lng": pickup_lng,
        "address": {
            "name": store_name,
            "line1": store_address,
            "line2": "",
            "city": city,
            "state": drop_state,
        },
        "pincode": pickup_pincode,
        "phone": (ticket.get("user_phone") or "").lstrip("+").replace(" ", ""),
    }

    drop_payload = {
        "lat": drop_lat,
        "lng": drop_lng,
        "address": {
            "name": customer_name or "Customer",
            "line1": customer_geo.get("formatted_address", customer_location),
            "line2": "",
            "city": drop_city,
            "state": drop_state,
        },
        "pincode": drop_pincode,
        "phone": (ticket.get("user_phone") or "").lstrip("+").replace(" ", ""),
    }

    callback_url = f"{Config.VAPI_SERVER_URL}/api/logistics/callback"

    order_items = [{
        "name": product_name,
        "qty": 1,
        "price": product_price,
    }]

    # --- Place the delivery order ---
    try:
        order_resp = await create_delivery_order(
            client_order_id=client_order_id,
            pickup=pickup_payload,
            drop=drop_payload,
            callback_url=callback_url,
            order_amount=product_price,
            order_weight=1.0,
            order_items=order_items,
            selected_lsp_id=cheapest["lsp_id"],
            quote_id=quote_id,
        )
    except Exception as e:
        logger.exception("ProRouting /createasync failed for ticket %s", ticket_id)
        update_logistics_order_error(logistics_id, f"Create order failed: {e}")
        update_ticket_status(ticket_id, "failed", error_message=f"Delivery order creation failed: {e}")
        return

    if order_resp.get("status") != 1:
        msg = order_resp.get("message", "Order creation failed")
        update_logistics_order_error(logistics_id, msg)
        update_ticket_status(ticket_id, "failed", error_message=msg)
        return

    # --- Success: update DB with order details ---
    order_data = order_resp.get("order", {})
    prorouting_order_id = order_data.get("id", "")
    order_state = order_data.get("state", "UnFulfilled")

    update_logistics_order_placed(
        logistics_order_id=logistics_id,
        prorouting_order_id=prorouting_order_id,
        order_state=order_state,
        quote_id=quote_id,
        selected_lsp_id=cheapest["lsp_id"],
        selected_lsp_name=cheapest.get("logistics_seller"),
        quoted_price=float(cheapest.get("price_forward", 0)),
    )

    update_ticket_status(ticket_id, "order_placed")
    logger.info(
        "Ticket %s: delivery order placed! prorouting_id=%s, lsp=%s, price=₹%s",
        ticket_id, prorouting_order_id,
        cheapest.get("logistics_seller"), cheapest.get("price_forward"),
    )


# ---------------------------------------------------------------------------
# Retry delivery after LSP cancellation
# ---------------------------------------------------------------------------

def _extract_city_from_address(address: str) -> str:
    """Extract city from a Google-formatted Indian address.

    'L73, 15th Cross Rd, Sector 6, HSR Layout, Bengaluru, Karnataka 560102, India'
    → 'Bengaluru'
    """
    parts = [p.strip() for p in (address or "").split(",")]
    if len(parts) >= 3:
        return parts[-3].strip()
    return parts[-1].strip() if parts else ""


async def retry_delivery(ticket_id: str) -> None:
    """
    Retry delivery with the next available LSP after a cancellation.
    Re-quotes, excludes previously failed LSPs, picks the cheapest remaining.
    """
    order = get_logistics_order(ticket_id)
    if not order:
        logger.error("Ticket %s: no logistics order found for retry", ticket_id)
        return

    failed_lsps = get_failed_lsp_ids(ticket_id)
    max_retries = Config.MAX_DELIVERY_RETRIES

    if len(failed_lsps) >= max_retries:
        logger.warning(
            "Ticket %s: max delivery retries (%d) reached, giving up. Failed LSPs: %s",
            ticket_id, max_retries, failed_lsps,
        )
        update_ticket_status(
            ticket_id, "delivery_failed",
            error_message=f"All delivery attempts failed after {len(failed_lsps)} retries",
        )
        return

    logger.info(
        "Ticket %s: retrying delivery (attempt %d/%d, excluding LSPs: %s)",
        ticket_id, len(failed_lsps) + 1, max_retries, failed_lsps,
    )
    update_ticket_status(ticket_id, "retrying_delivery")

    pickup_lat = order["pickup_lat"]
    pickup_lng = order["pickup_lng"]
    drop_lat = order["drop_lat"]
    drop_lng = order["drop_lng"]
    city = _extract_city_from_address(order.get("drop_address") or "")

    # --- Fresh quotes ---
    try:
        quotes_resp = await get_delivery_quotes(
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            pickup_pincode=order["pickup_pincode"] or "000000",
            drop_lat=drop_lat,
            drop_lng=drop_lng,
            drop_pincode=order["drop_pincode"] or "000000",
            city=city,
            order_amount=order["order_amount"] or 0,
            order_weight=order["order_weight"] or 1.0,
        )
    except Exception as e:
        logger.exception("Ticket %s: re-quote failed during retry", ticket_id)
        update_ticket_status(ticket_id, "delivery_failed", error_message=f"Retry quotes failed: {e}")
        return

    if quotes_resp.get("status") != 1 or not quotes_resp.get("quotes"):
        msg = quotes_resp.get("message", "No delivery partners available on retry")
        update_ticket_status(ticket_id, "delivery_failed", error_message=msg)
        return

    # --- Filter out failed LSPs and pick cheapest ---
    available = [q for q in quotes_resp["quotes"] if q.get("lsp_id") not in failed_lsps]
    if not available:
        logger.warning("Ticket %s: no LSPs left after excluding %s", ticket_id, failed_lsps)
        update_ticket_status(
            ticket_id, "delivery_failed",
            error_message=f"No delivery partners left (excluded {len(failed_lsps)} failed LSPs)",
        )
        return

    cheapest = min(available, key=lambda q: float(q.get("price_forward", 999999)))
    quote_id = quotes_resp.get("quote_id")

    logger.info(
        "Ticket %s retry: picked LSP = %s (₹%s, pickup ETA %s min)",
        ticket_id, cheapest.get("logistics_seller"),
        cheapest.get("price_forward"), cheapest.get("pickup_eta"),
    )

    # --- New logistics order record ---
    short_uid = uuid.uuid4().hex[:8]
    client_order_id = f"{ticket_id}_{short_uid}"

    logistics_id = create_logistics_order(
        ticket_id=ticket_id,
        store_call_id=order["store_call_id"],
        client_order_id=client_order_id,
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        pickup_address=order["pickup_address"],
        pickup_pincode=order["pickup_pincode"],
        pickup_phone=order["pickup_phone"],
        drop_lat=drop_lat,
        drop_lng=drop_lng,
        drop_address=order["drop_address"],
        drop_pincode=order["drop_pincode"],
        drop_phone=order["drop_phone"],
        customer_name=order["customer_name"],
        order_amount=order["order_amount"],
        order_weight=order["order_weight"],
    )

    # --- Build payloads reusing stored addresses ---
    drop_state = ""
    addr_parts = (order.get("drop_address") or "").split(",")
    if len(addr_parts) >= 2:
        drop_state = addr_parts[-2].strip().split()[0] if addr_parts[-2].strip() else ""

    pickup_payload = {
        "lat": pickup_lat,
        "lng": pickup_lng,
        "address": {
            "name": "Store",
            "line1": order["pickup_address"] or "",
            "line2": "",
            "city": city,
            "state": drop_state,
        },
        "pincode": order["pickup_pincode"] or "000000",
        "phone": (order["pickup_phone"] or "").lstrip("+").replace(" ", ""),
    }

    drop_payload = {
        "lat": drop_lat,
        "lng": drop_lng,
        "address": {
            "name": order["customer_name"] or "Customer",
            "line1": order["drop_address"] or "",
            "line2": "",
            "city": city,
            "state": drop_state,
        },
        "pincode": order["drop_pincode"] or "000000",
        "phone": (order["drop_phone"] or "").lstrip("+").replace(" ", ""),
    }

    callback_url = f"{Config.VAPI_SERVER_URL}/api/logistics/callback"

    product = get_product(ticket_id)
    product_name = product["product_name"] if product else "Item"

    order_items = [{
        "name": product_name,
        "qty": 1,
        "price": order["order_amount"] or 0,
    }]

    # --- Place the new delivery order ---
    try:
        order_resp = await create_delivery_order(
            client_order_id=client_order_id,
            pickup=pickup_payload,
            drop=drop_payload,
            callback_url=callback_url,
            order_amount=order["order_amount"] or 0,
            order_weight=order["order_weight"] or 1.0,
            order_items=order_items,
            selected_lsp_id=cheapest["lsp_id"],
            quote_id=quote_id,
        )
    except Exception as e:
        logger.exception("Ticket %s: retry create order failed", ticket_id)
        update_logistics_order_error(logistics_id, f"Retry create order failed: {e}")
        update_ticket_status(ticket_id, "delivery_failed", error_message=f"Retry order creation failed: {e}")
        return

    if order_resp.get("status") != 1:
        msg = order_resp.get("message", "Retry order creation failed")
        update_logistics_order_error(logistics_id, msg)
        update_ticket_status(ticket_id, "delivery_failed", error_message=msg)
        return

    # --- Success ---
    order_data = order_resp.get("order", {})
    new_prorouting_id = order_data.get("id", "")
    new_state = order_data.get("state", "UnFulfilled")

    update_logistics_order_placed(
        logistics_order_id=logistics_id,
        prorouting_order_id=new_prorouting_id,
        order_state=new_state,
        quote_id=quote_id,
        selected_lsp_id=cheapest["lsp_id"],
        selected_lsp_name=cheapest.get("logistics_seller"),
        quoted_price=float(cheapest.get("price_forward", 0)),
    )

    update_ticket_status(ticket_id, "order_placed")
    logger.info(
        "Ticket %s: RETRY delivery placed! prorouting_id=%s, lsp=%s, price=₹%s (attempt %d)",
        ticket_id, new_prorouting_id,
        cheapest.get("logistics_seller"), cheapest.get("price_forward"),
        len(failed_lsps) + 1,
    )
