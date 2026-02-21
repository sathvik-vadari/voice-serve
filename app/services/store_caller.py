"""Store Caller – orchestrates outbound VAPI calls to stores for product inquiry."""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from app.helpers.config import Config
from app.helpers.prompt_loader import PromptLoader
from app.helpers.regional import detect_region
from app.db.tickets import (
    create_store_call,
    update_store_call_vapi_id,
    update_store_call_status,
    get_stores,
    log_tool_call,
)
from app.services.vapi_client import create_store_phone_call

logger = logging.getLogger(__name__)

MAX_STORES = Config.MAX_STORES_TO_CALL


def _build_store_prompt(
    product: dict[str, Any], location: str, store_name: str,
) -> tuple[str, dict[str, Any], str]:
    """
    Fill the store_caller prompt template with product details, store name, and regional context.
    Returns (prompt_string, regional_profile, first_message_for_vapi).
    """
    loader = PromptLoader()
    template = loader.load_prompt("store_caller") or ""

    region = detect_region(location)

    specs = product.get("specs") or {}
    specs_lines = []
    for key, val in specs.items():
        specs_lines.append(f"  - {key}: {val}")
    specs_str = "\n".join(specs_lines) if specs_lines else "  (no specific details provided)"

    alts = product.get("alternatives") or []
    alts_str = "\n".join(
        f"  {i+1}. {a['name']} (avg ₹{a.get('avg_price', 'N/A')}) – {a.get('reason', '')}"
        for i, a in enumerate(alts)
    ) or "None"

    product_name = product.get("product_name", "the requested product")

    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    current_datetime = ist_now.strftime("%A, %d %B %Y, %I:%M %p IST")

    prompt = template.replace("{current_datetime}", current_datetime)
    prompt = prompt.replace("{product_name}", product_name)
    prompt = prompt.replace("{product_specs}", specs_str)
    prompt = prompt.replace("{alternatives}", alts_str)
    prompt = prompt.replace("{location}", location)
    prompt = prompt.replace("{store_name}", store_name)
    prompt = prompt.replace("{city}", region.get("display_name", "India"))
    prompt = prompt.replace("{regional_language}", region.get("regional_language", "hindi"))
    prompt = prompt.replace("{greeting}", region.get("greeting", "Namaste ji!"))
    prompt = prompt.replace("{communication_style}", region.get("communication_style", "Speak in Hindi mixed with English."))
    prompt = prompt.replace("{thank_you}", region.get("thank_you", "Bahut dhanyavaad ji!"))
    prompt = prompt.replace("{busy_response}", region.get("busy_response", "Koi baat nahi ji, dhanyavaad!"))

    first_message = f"Hello ji, namaste! Yeh {store_name} hai kya?"

    return prompt, region, first_message


async def call_stores(
    ticket_id: str, product: dict[str, Any], location: str,
    *, test_mode: bool = False, test_phone: str | None = None,
) -> list[dict[str, Any]]:
    """
    Initiate VAPI calls to stores saved for this ticket.

    In test_mode: only places ONE call to test_phone (using the first store's
    context) so you can hear the bot without calling real stores.
    """
    stores = get_stores(ticket_id)
    if not stores:
        logger.warning("No stores to call for ticket %s", ticket_id)
        return []

    if test_mode:
        targets = stores[:1]
    else:
        targets = stores[:MAX_STORES]
        if len(stores) > MAX_STORES:
            logger.info(
                "Ticket %s has %d stores, capping to MAX_STORES_TO_CALL=%d",
                ticket_id, len(stores), MAX_STORES,
            )

    results = []

    for store in targets:
        phone = test_phone if test_mode else store.get("phone_number")
        if not phone:
            logger.warning("Skipping store %s – no phone number", store["store_name"])
            continue

        prompt, region, first_message = _build_store_prompt(
            product, location, store["store_name"],
        )
        store_call_id = create_store_call(ticket_id, store["id"])

        if test_mode:
            logger.info(
                "TEST MODE: calling %s instead of real store %s (%s)",
                phone, store["store_name"], store.get("phone_number"),
            )

        try:
            vapi_result = await create_store_phone_call(
                customer_number=phone,
                system_prompt=prompt,
                ticket_id=ticket_id,
                store_call_id=store_call_id,
                region=region,
                first_message=first_message,
            )

            if vapi_result.get("success"):
                vapi_call_id = vapi_result.get("call", {}).get("id")
                if vapi_call_id:
                    update_store_call_vapi_id(store_call_id, vapi_call_id)
                status = "calling"
            else:
                logger.error(
                    "VAPI call failed for store %s (phone=%s): %s | %s",
                    store["store_name"], phone,
                    vapi_result.get("error"), vapi_result.get("body", ""),
                )
                update_store_call_status(store_call_id, "failed")
                status = "failed"

            log_tool_call(
                ticket_id, "vapi_create_store_call",
                {"store": store["store_name"], "phone": phone, "test_mode": test_mode, "region": region.get("region_key")},
                vapi_result,
                status="success" if vapi_result.get("success") else "error",
                error_message=vapi_result.get("error"),
                store_call_id=store_call_id,
            )

            results.append({
                "store_id": store["id"],
                "store_name": store["store_name"],
                "store_call_id": store_call_id,
                "vapi_call_id": vapi_result.get("call", {}).get("id"),
                "status": status,
            })

        except Exception as e:
            logger.exception("Failed to call store %s", store["store_name"])
            update_store_call_status(store_call_id, "failed")
            log_tool_call(
                ticket_id, "vapi_create_store_call",
                {"store": store["store_name"], "phone": phone, "test_mode": test_mode},
                {"error": str(e)},
                status="error", error_message=str(e), store_call_id=store_call_id,
            )
            results.append({
                "store_id": store["id"],
                "store_name": store["store_name"],
                "store_call_id": store_call_id,
                "status": "failed",
            })

    return results
