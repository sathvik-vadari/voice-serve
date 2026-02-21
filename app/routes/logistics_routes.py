"""ProRouting logistics callback webhook + status polling."""
import asyncio
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
from app.services.logistics import retry_delivery

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


def _is_lsp_cancellation(body: dict[str, Any]) -> bool:
    """Check if the cancellation was initiated by the LSP (not the buyer/merchant)."""
    order = body.get("order", {})
    cancellation = order.get("cancellation", {})
    cancelled_by = cancellation.get("cancelled_by", "")
    lsp_id = order.get("lsp", {}).get("id", "")
    return bool(cancelled_by and lsp_id and cancelled_by == lsp_id)


@router.post("/api/logistics/callback")
async def logistics_callback(request: Request):
    """
    Webhook endpoint for ProRouting status callbacks.
    ProRouting hits this URL whenever the delivery order state changes
    (agent assigned, picked up, delivered, cancelled, etc.).

    On LSP-initiated cancellations, automatically retries with the next
    available delivery partner.
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

    rider_info = body.get("order", {}).get("rider", {}) or body.get("agent", {}) or {}
    rider_name = rider_info.get("name") or None
    rider_phone = rider_info.get("phone") or None
    tracking_url = body.get("order", {}).get("tracking_url") or body.get("tracking_url")

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
            if order_state == "Cancelled" and _is_lsp_cancellation(body):
                cancellation = body.get("order", {}).get("cancellation", {})
                logger.info(
                    "Ticket %s: LSP cancelled (reason: %s) — triggering auto-retry",
                    ticket_id, cancellation.get("reason_desc", "unknown"),
                )
                asyncio.create_task(retry_delivery(ticket_id))
            else:
                ticket_status = PROROUTING_TO_TICKET_STATUS.get(order_state)
                if ticket_status:
                    update_ticket_status(ticket_id, ticket_status)
                    logger.info(
                        "Ticket %s: delivery state → %s (ticket status → %s)",
                        ticket_id, order_state, ticket_status,
                    )

    return {"status": "ok"}
