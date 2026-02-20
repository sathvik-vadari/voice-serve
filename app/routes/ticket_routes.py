"""Ticket API – the main entry point for the frontend."""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.helpers.logger import setup_logger
from app.db.tickets import (
    create_ticket,
    get_ticket,
    update_ticket_status,
    update_ticket_query_type,
    get_store_calls_for_ticket,
    get_product,
)
from app.services.orchestrator import classify_query
from app.services.product_research import research_product
from app.services.google_maps import find_stores
from app.services.store_caller import call_stores

logger = setup_logger(__name__)

router = APIRouter(tags=["tickets"])


class TicketRequest(BaseModel):
    query: str
    location: str
    ticket_id: str
    user_phone: str


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
    create_ticket(req.ticket_id, req.query, req.location, req.user_phone)
    logger.info("Ticket %s created: query=%r location=%r", req.ticket_id, req.query, req.location)

    bg.add_task(_process_ticket, req.ticket_id, req.query, req.location, req.user_phone)

    return TicketResponse(
        ticket_id=req.ticket_id,
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

    if ticket.get("error_message"):
        response["error"] = ticket["error_message"]

    if ticket.get("final_result"):
        response["result"] = ticket["final_result"]

    # Include live progress for order_product tickets
    if ticket.get("query_type") == "order_product" and ticket["status"] not in ("completed", "failed"):
        product = get_product(ticket_id)
        calls = get_store_calls_for_ticket(ticket_id)
        response["progress"] = {
            "product_identified": product is not None,
            "product_name": product["product_name"] if product else None,
            "stores_found": len(calls),
            "calls_completed": sum(1 for c in calls if c["status"] in ("analyzed", "failed")),
            "calls_in_progress": sum(1 for c in calls if c["status"] not in ("analyzed", "failed")),
        }

    return response


# ---------------------------------------------------------------------------
# Background pipeline
# ---------------------------------------------------------------------------

async def _process_ticket(ticket_id: str, query: str, location: str, user_phone: str) -> None:
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
            await _handle_order(ticket_id, query, location)

    except Exception as e:
        logger.exception("Pipeline failed for ticket %s", ticket_id)
        update_ticket_status(ticket_id, "failed", error_message=str(e))


async def _handle_wakeup(ticket_id: str, query: str, user_phone: str) -> None:
    """Handle wake-up/alarm/reminder flow using existing VAPI infrastructure."""
    from app.services.vapi_client import create_phone_call
    from app.helpers.prompt_loader import PromptLoader
    from app.db.tickets import set_ticket_final_result

    update_ticket_status(ticket_id, "wakeup_calling")

    loader = PromptLoader()
    system_prompt = loader.get_default_prompt()

    result = await create_phone_call(user_phone, system_prompt)
    if result.get("success"):
        set_ticket_final_result(ticket_id, {
            "status": "wakeup_call_initiated",
            "vapi_call_id": result.get("call", {}).get("id"),
            "message": "Wake-up call placed successfully. VAPI is handling the conversation.",
        })
    else:
        update_ticket_status(ticket_id, "failed", error_message=result.get("error"))


async def _handle_order(ticket_id: str, query: str, location: str) -> None:
    """Handle order/product flow: research → find stores → call stores."""
    # Step 2a: Product research
    update_ticket_status(ticket_id, "researching")
    product = await research_product(ticket_id, query)
    logger.info(
        "Ticket %s product: %s (avg ₹%s, %d alternatives)",
        ticket_id, product.get("product_name"),
        product.get("avg_price_online"), len(product.get("alternatives") or []),
    )

    # Step 2b: Find stores via Google Maps
    update_ticket_status(ticket_id, "finding_stores")
    stores = await find_stores(
        ticket_id,
        product.get("store_search_query", "store"),
        location,
    )
    logger.info("Ticket %s: found %d callable stores", ticket_id, len(stores))

    if not stores:
        from app.db.tickets import set_ticket_final_result
        set_ticket_final_result(ticket_id, {
            "status": "no_stores",
            "message": "Could not find any stores with phone numbers near the given location.",
            "product": product.get("product_name"),
        })
        return

    # Step 2c: Call stores via VAPI
    update_ticket_status(ticket_id, "calling_stores")
    call_results = await call_stores(ticket_id, product, location)
    logger.info(
        "Ticket %s: initiated %d store calls (%d successful)",
        ticket_id, len(call_results),
        sum(1 for r in call_results if r["status"] == "calling"),
    )

    active_calls = [r for r in call_results if r["status"] == "calling"]
    if not active_calls:
        from app.db.tickets import set_ticket_final_result
        set_ticket_final_result(ticket_id, {
            "status": "call_failed",
            "message": "All store calls failed to initiate.",
            "product": product.get("product_name"),
        })
