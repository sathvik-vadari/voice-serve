"""Ticket API – the main entry point for the frontend."""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.helpers.config import Config
from app.helpers.logger import setup_logger
from app.db.tickets import (
    create_ticket,
    get_ticket,
    get_next_ticket_id,
    ticket_exists_and_active,
    update_ticket_status,
    update_ticket_query_type,
    get_store_calls_for_ticket,
    get_product,
    get_stores,
    get_logistics_order,
    get_web_deals,
)
import asyncio

from app.services.orchestrator import classify_query
from app.services.product_research import research_product
from app.services.google_maps import find_stores
from app.services.store_caller import call_stores
from app.services.gemini_client import analyze_query, rerank_stores
from app.services.web_deals import search_web_deals
from app.services.options_summary import generate_options_summary
from app.services.logistics import place_order

logger = setup_logger(__name__)

router = APIRouter(tags=["tickets"])


class TicketRequest(BaseModel):
    query: str
    location: str
    ticket_id: Optional[str] = None
    user_phone: str
    user_name: Optional[str] = None
    test_mode: Optional[bool] = None
    test_phone: Optional[str] = None
    max_stores: Optional[int] = None


class TicketResponse(BaseModel):
    ticket_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# POST /api/ticket – accept a new ticket and start processing
# ---------------------------------------------------------------------------

@router.post("/api/ticket", response_model=TicketResponse)
async def create_ticket_endpoint(req: TicketRequest, bg: BackgroundTasks):
    """
    Accept a user query from the frontend, classify it, and kick off the
    appropriate pipeline (wakeup or order) in the background.
    """
    ticket_id = req.ticket_id.strip() if req.ticket_id else ""

    if not ticket_id:
        ticket_id = get_next_ticket_id()
        logger.info("Auto-generated ticket_id: %s", ticket_id)

    if ticket_exists_and_active(ticket_id):
        return TicketResponse(
            ticket_id=ticket_id,
            status="rejected",
            message=f"Ticket {ticket_id} is already being processed. Wait for it to complete or use a new ticket ID.",
        )

    create_ticket(ticket_id, req.query, req.location, req.user_phone, req.user_name)
    logger.info("Ticket %s created: query=%r location=%r", ticket_id, req.query, req.location)

    is_test = req.test_mode if req.test_mode is not None else Config.TEST_MODE
    test_phone = req.test_phone or Config.TEST_PHONE or None

    if is_test:
        logger.info("TEST MODE active (phone=%s) – will not call real stores", test_phone)

    max_stores = req.max_stores
    if max_stores is not None:
        max_stores = max(1, min(10, max_stores))

    bg.add_task(
        _process_ticket, ticket_id, req.query, req.location, req.user_phone,
        test_mode=is_test, test_phone=test_phone, max_stores=max_stores,
    )

    return TicketResponse(
        ticket_id=ticket_id,
        status="processing",
        message="Ticket received. Processing started.",
    )


# ---------------------------------------------------------------------------
# GET /api/ticket/{ticket_id} – poll for status / results
# ---------------------------------------------------------------------------

@router.get("/api/ticket/{ticket_id}")
async def get_ticket_status(ticket_id: str):
    ticket = get_ticket(ticket_id)
    if not ticket:
        return {"error": "Ticket not found", "ticket_id": ticket_id}

    response: dict = {
        "ticket_id": ticket["ticket_id"],
        "status": ticket["status"],
        "query_type": ticket.get("query_type"),
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
    }

    if ticket.get("vapi_call_id"):
        response["vapi_call_id"] = ticket["vapi_call_id"]

    if ticket.get("transcript"):
        response["transcript"] = ticket["transcript"]

    if ticket.get("tool_calls_made"):
        response["tool_calls_made"] = ticket["tool_calls_made"]

    if ticket.get("error_message"):
        response["error"] = ticket["error_message"]

    if ticket.get("final_result"):
        response["result"] = ticket["final_result"]

    # Always include product + store details once available
    if ticket.get("query_type") == "order_product":
        product = get_product(ticket_id)
        if product:
            response["product"] = product

        stores = get_stores(ticket_id)
        if stores:
            response["stores"] = stores

        calls = get_store_calls_for_ticket(ticket_id)
        if calls:
            response["store_calls"] = calls
            response["progress"] = {
                "stores_found": len(stores),
                "calls_total": len(calls),
                "calls_completed": sum(1 for c in calls if c["status"] in ("analyzed", "failed")),
                "calls_in_progress": sum(1 for c in calls if c["status"] not in ("analyzed", "failed")),
            }

        web_deals = get_web_deals(ticket_id)
        if web_deals and web_deals.get("deals"):
            response["web_deals"] = {
                "search_summary": web_deals.get("search_summary"),
                "deals": web_deals.get("deals", []),
                "best_deal": web_deals.get("best_deal"),
                "surprise_finds": web_deals.get("surprise_finds"),
                "price_range": web_deals.get("price_range"),
                "status": web_deals.get("status"),
            }

        logistics = get_logistics_order(ticket_id)
        if logistics:
            response["delivery"] = {
                "order_state": logistics.get("order_state"),
                "logistics_partner": logistics.get("selected_lsp_name"),
                "delivery_price": logistics.get("quoted_price"),
                "rider_name": logistics.get("rider_name"),
                "rider_phone": logistics.get("rider_phone"),
                "tracking_url": logistics.get("tracking_url"),
                "prorouting_order_id": logistics.get("prorouting_order_id"),
            }

    return response


# ---------------------------------------------------------------------------
# GET /api/ticket/{ticket_id}/options – user-facing summary of all options
# ---------------------------------------------------------------------------

@router.get("/api/ticket/{ticket_id}/options")
async def get_ticket_options(ticket_id: str):
    """
    Once all calls are done, returns the successful options with a
    generated user-facing message summarizing everything.
    """
    result = await generate_options_summary(ticket_id)
    if "error" in result:
        status_code = 404 if result["error"] == "Ticket not found" else 400
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=status_code, content=result)
    return result


# ---------------------------------------------------------------------------
# POST /api/ticket/{ticket_id}/confirm – user confirms an option, triggers delivery
# ---------------------------------------------------------------------------

class ConfirmRequest(BaseModel):
    store_call_id: Optional[int] = None
    selected_option: Optional[int] = None
    customer_name: Optional[str] = None


@router.post("/api/ticket/{ticket_id}/confirm")
async def confirm_ticket_option(ticket_id: str, req: ConfirmRequest):
    """
    User confirms which store option to buy from.
    Pass either store_call_id (preferred, from options response) or
    selected_option (1-based index into the options list).
    Runs synchronously — returns the actual delivery booking result.
    """
    from fastapi.responses import JSONResponse

    if not req.store_call_id and not req.selected_option:
        return JSONResponse(
            status_code=400,
            content={"error": "Provide either store_call_id or selected_option"},
        )

    ticket = get_ticket(ticket_id)
    if not ticket:
        return JSONResponse(status_code=404, content={"error": "Ticket not found"})

    already_ordering = ("placing_order", "order_placed", "agent_assigned", "out_for_delivery")
    if ticket["status"] in already_ordering:
        return JSONResponse(
            status_code=409,
            content={"error": f"A delivery is already in progress (status: {ticket['status']})", "ticket_id": ticket_id},
        )

    allowed = ("completed", "failed", "delivery_failed")
    if ticket["status"] not in allowed:
        return JSONResponse(
            status_code=400,
            content={"error": f"Ticket cannot be confirmed in status '{ticket['status']}'", "ticket_id": ticket_id},
        )

    resolved_name = req.customer_name or ticket.get("user_name")
    if not resolved_name:
        return JSONResponse(
            status_code=400,
            content={"error": "customer_name is required (not provided at ticket creation or confirmation)"},
        )

    update_ticket_status(ticket_id, "placing_order")

    try:
        await place_order(
            ticket_id,
            store_call_id=req.store_call_id,
            selected_option=req.selected_option,
            customer_name=resolved_name,
        )
    except Exception as e:
        logger.exception("Delivery order placement failed for ticket %s", ticket_id)
        update_ticket_status(ticket_id, "failed", error_message=f"Delivery booking failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Delivery booking failed: {e}", "ticket_id": ticket_id},
        )

    logistics = get_logistics_order(ticket_id)
    if not logistics or logistics.get("order_state") == "failed":
        error_msg = (logistics or {}).get("error_message", "Unknown error during delivery booking")
        return JSONResponse(
            status_code=500,
            content={"error": error_msg, "ticket_id": ticket_id},
        )

    return {
        "ticket_id": ticket_id,
        "status": "order_placed",
        "customer_name": resolved_name,
        "delivery": {
            "prorouting_order_id": logistics.get("prorouting_order_id"),
            "logistics_partner": logistics.get("selected_lsp_name"),
            "delivery_price": logistics.get("quoted_price"),
            "order_state": logistics.get("order_state"),
            "pickup_address": logistics.get("pickup_address"),
            "drop_address": logistics.get("drop_address"),
        },
        "message": f"Order confirmed! {logistics.get('selected_lsp_name') or 'Delivery partner'} will pick up from the store. Delivery cost: ₹{logistics.get('quoted_price') or 'N/A'}.",
    }


# ---------------------------------------------------------------------------
# GET /api/ticket/{ticket_id}/delivery – delivery status & tracking
# ---------------------------------------------------------------------------

@router.get("/api/ticket/{ticket_id}/delivery")
async def get_delivery_status(ticket_id: str):
    """Get the logistics/delivery details for a ticket."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Ticket not found"})

    logistics = get_logistics_order(ticket_id)
    if not logistics:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=404,
            content={"error": "No delivery order found for this ticket", "ticket_id": ticket_id},
        )

    response = {
        "ticket_id": ticket_id,
        "ticket_status": ticket["status"],
        "delivery": {
            "order_state": logistics.get("order_state"),
            "prorouting_order_id": logistics.get("prorouting_order_id"),
            "client_order_id": logistics.get("client_order_id"),
            "logistics_partner": logistics.get("selected_lsp_name"),
            "delivery_price": logistics.get("quoted_price"),
            "pickup": {
                "store_name": None,
                "address": logistics.get("pickup_address"),
                "phone": logistics.get("pickup_phone"),
            },
            "drop": {
                "customer_name": logistics.get("customer_name"),
                "address": logistics.get("drop_address"),
                "phone": logistics.get("drop_phone"),
            },
            "rider": None,
            "tracking_url": logistics.get("tracking_url"),
            "error": logistics.get("error_message"),
            "created_at": logistics.get("created_at"),
            "updated_at": logistics.get("updated_at"),
        },
    }

    if logistics.get("rider_name") or logistics.get("rider_phone"):
        response["delivery"]["rider"] = {
            "name": logistics.get("rider_name"),
            "phone": logistics.get("rider_phone"),
        }

    return response


# ---------------------------------------------------------------------------
# Background pipeline
# ---------------------------------------------------------------------------

async def _process_ticket(
    ticket_id: str, query: str, location: str, user_phone: str,
    *, test_mode: bool = False, test_phone: Optional[str] = None,
    max_stores: Optional[int] = None,
) -> None:
    """Full async pipeline: classify → research → find stores → call stores."""
    try:
        # Step 1: Classify query
        update_ticket_status(ticket_id, "classifying")
        classification = await classify_query(ticket_id, query)
        query_type = classification["category"]
        update_ticket_query_type(ticket_id, query_type)
        logger.info("Ticket %s classified as %s (confidence=%.2f)", ticket_id, query_type, classification.get("confidence", 0))

        # Step 2: Route to the right flow
        if query_type == "wakeup_alarm":
            await _handle_wakeup(ticket_id, query, user_phone)
        else:
            await _handle_order(ticket_id, query, location, test_mode=test_mode, test_phone=test_phone, max_stores=max_stores)

    except Exception as e:
        logger.exception("Pipeline failed for ticket %s", ticket_id)
        update_ticket_status(ticket_id, "failed", error_message=str(e))


async def _handle_wakeup(ticket_id: str, query: str, user_phone: str) -> None:
    """Handle wake-up/alarm/reminder flow using existing VAPI infrastructure."""
    from app.services.vapi_client import create_phone_call
    from app.helpers.prompt_loader import PromptLoader
    from app.db.tickets import set_ticket_vapi_call_id

    update_ticket_status(ticket_id, "wakeup_calling")

    loader = PromptLoader()
    system_prompt = loader.get_default_prompt()

    result = await create_phone_call(user_phone, system_prompt)
    if result.get("success"):
        vapi_call_id = result.get("call", {}).get("id")
        if vapi_call_id:
            set_ticket_vapi_call_id(ticket_id, vapi_call_id)
        update_ticket_status(ticket_id, "wakeup_in_progress")
        logger.info("Wakeup call placed for ticket %s (vapi_call_id=%s) – waiting for call to end", ticket_id, vapi_call_id)
    else:
        update_ticket_status(ticket_id, "failed", error_message=result.get("error"))


async def _handle_order(
    ticket_id: str, query: str, location: str,
    *, test_mode: bool = False, test_phone: Optional[str] = None,
    max_stores: Optional[int] = None,
) -> None:
    """Handle order/product flow: analyze → research → [web search + find stores + call] in parallel."""

    # Step 2a: Gemini query intelligence
    query_analysis = None
    try:
        update_ticket_status(ticket_id, "analyzing")
        query_analysis = await analyze_query(ticket_id, query, location)
        logger.info(
            "Ticket %s query analysis: type=%s, specific_store=%s, queries=%s",
            ticket_id,
            query_analysis.get("query_type"),
            query_analysis.get("specific_store_name"),
            query_analysis.get("search_queries"),
        )
    except Exception as e:
        logger.warning("Gemini query analysis failed for ticket %s, continuing without it: %s", ticket_id, e)

    # Step 2b: Product research (with query analysis context)
    update_ticket_status(ticket_id, "researching")
    product = await research_product(ticket_id, query, query_analysis=query_analysis)
    logger.info(
        "Ticket %s product: %s (specific_store=%s, avg ₹%s, %d alternatives)",
        ticket_id, product.get("product_name"),
        product.get("is_specific_store"),
        product.get("avg_price_online"), len(product.get("alternatives") or []),
    )

    # Step 2c: Launch web deals search in parallel with store discovery + calling.
    # The web search runs independently — if it fails, store calling continues.
    web_deals_task = asyncio.create_task(
        _search_web_deals_safe(ticket_id, query, product, location)
    )

    # Step 2d: Find stores via Google Maps (multi-strategy)
    update_ticket_status(ticket_id, "finding_stores")

    search_queries = None
    if query_analysis and query_analysis.get("search_queries"):
        search_queries = query_analysis["search_queries"]
    elif product.get("_search_queries"):
        search_queries = product["_search_queries"]

    specific_store_name = None
    if query_analysis and query_analysis.get("is_specific_store"):
        specific_store_name = query_analysis.get("specific_store_name")

    stores = await find_stores(
        ticket_id,
        product.get("store_search_query", "store"),
        location,
        max_stores=max_stores,
        search_queries=search_queries,
        specific_store_name=specific_store_name,
    )
    logger.info("Ticket %s: found %d callable stores", ticket_id, len(stores))

    # Step 2e: Gemini re-ranking (prioritize exact store matches)
    if query_analysis and stores and len(stores) > 1:
        try:
            reranked = await rerank_stores(ticket_id, query, stores, query_analysis)
            ordered_place_ids = [s.get("place_id") for s in reranked if s.get("place_id")]
            if ordered_place_ids:
                from app.db.tickets import update_store_priorities
                update_store_priorities(ticket_id, ordered_place_ids)
            logger.info("Ticket %s: stores re-ranked by Gemini", ticket_id)
        except Exception as e:
            logger.warning("Store re-ranking failed for ticket %s: %s", ticket_id, e)

    if not stores:
        # Even with no stores, wait for web deals — they might still help
        web_deals = await web_deals_task
        from app.db.tickets import set_ticket_final_result
        result = {
            "status": "no_stores",
            "message": "Could not find any stores with phone numbers near the given location.",
            "product": product.get("product_name"),
        }
        if web_deals and web_deals.get("deals"):
            result["status"] = "web_deals_only"
            result["message"] = (
                "No local stores found, but we found online deals for you!"
            )
            result["web_deals"] = web_deals
        set_ticket_final_result(ticket_id, result)
        return

    # Step 2f: Call stores via VAPI
    if test_mode:
        logger.info(
            "TEST MODE: Found %d stores but will only call test number %s",
            len(stores), test_phone,
        )
        update_ticket_status(ticket_id, "calling_stores")
        call_results = await call_stores(
            ticket_id, product, location,
            test_mode=True, test_phone=test_phone or Config.TEST_PHONE,
            max_stores=max_stores,
        )
    else:
        update_ticket_status(ticket_id, "calling_stores")
        call_results = await call_stores(ticket_id, product, location, max_stores=max_stores)

    logger.info(
        "Ticket %s: initiated %d store calls (%d successful)",
        ticket_id, len(call_results),
        sum(1 for r in call_results if r["status"] == "calling"),
    )

    active_calls = [r for r in call_results if r["status"] == "calling"]
    if not active_calls:
        web_deals = await web_deals_task
        from app.db.tickets import set_ticket_final_result
        result = {
            "status": "call_failed",
            "message": "All store calls failed to initiate.",
            "product": product.get("product_name"),
        }
        if web_deals and web_deals.get("deals"):
            result["web_deals"] = web_deals
        set_ticket_final_result(ticket_id, result)


async def _search_web_deals_safe(
    ticket_id: str, query: str, product: dict, location: str,
) -> dict:
    """Wrapper that never raises — web deals are best-effort alongside store calls."""
    try:
        result = await search_web_deals(ticket_id, query, product, location)
        logger.info("Ticket %s: web deals search completed", ticket_id)
        return result
    except Exception as e:
        logger.warning("Web deals search failed for ticket %s: %s", ticket_id, e)
        return {"deals": [], "error": str(e)}
