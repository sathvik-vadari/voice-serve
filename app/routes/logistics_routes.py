"""ProRouting logistics callback webhook + status polling."""
import logging
from typing import Any

from fastapi import APIRouter, Request

from app.helpers.logger import setup_logger
from app.db.tickets import (
    update_ticket_status,
    update_logistics_order_status,
    append_logistics_callback,
    get_logistics_order_by_prorouting_id,
)

logger = setup_logger(__name__)

router = APIRouter(tags=["logistics"])

PROROUTING_TO_TICKET_STATUS = {
    "UnFulfilled": "order_placed",
    "Pending": "order_placed",
    "Searching-for-Agent": "order_placed",
    "Agent-assigned": "agent_assigned",
    "At-pickup": "agent_assigned",
    "Order-picked-up": "out_for_delivery",
    "At-delivery": "out_for_delivery",
    "Order-delivered": "delivered",
    "Cancelled": "delivery_failed",
    "RTO-Initiated": "delivery_failed",
    "RTO-Delivered": "delivery_failed",
    "RTO-Disposed": "delivery_failed",
}


@router.post("/api/logistics/callback")
async def logistics_callback(request: Request):
    """
    Webhook endpoint for ProRouting status callbacks.
    ProRouting hits this URL whenever the delivery order state changes
    (agent assigned, picked up, delivered, cancelled, etc.).
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        logger.warning("Logistics callback: invalid JSON body")
        return {"status": "error", "message": "Invalid JSON"}

    logger.info("Logistics callback received: %s", body)

    order_id = body.get("order_id") or body.get("order", {}).get("id")
    if not order_id:
        logger.warning("Logistics callback: no order_id found in payload")
        return {"status": "ok"}

    order_state = (
        body.get("state")
        or body.get("order", {}).get("state")
        or body.get("order_state")
    )

    rider_name = body.get("agent", {}).get("name") if body.get("agent") else None
    rider_phone = body.get("agent", {}).get("phone") if body.get("agent") else None
    tracking_url = body.get("tracking_url") or body.get("tracking", {}).get("url") if body.get("tracking") else None

    append_logistics_callback(order_id, body)

    if order_state:
        ticket_id = update_logistics_order_status(
            prorouting_order_id=order_id,
            order_state=order_state,
            rider_name=rider_name,
            rider_phone=rider_phone,
            tracking_url=tracking_url,
        )

        if ticket_id:
            ticket_status = PROROUTING_TO_TICKET_STATUS.get(order_state)
            if ticket_status:
                update_ticket_status(ticket_id, ticket_status)
                logger.info(
                    "Ticket %s: delivery state → %s (ticket status → %s)",
                    ticket_id, order_state, ticket_status,
                )

    return {"status": "ok"}
