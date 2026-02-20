"""Store Caller – orchestrates outbound VAPI calls to stores for product inquiry."""
import json
import logging
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


def _build_store_prompt(product: dict[str, Any], location: str) -> tuple[str, dict[str, Any]]:
    """
    Fill the store_caller prompt template with product details and regional context.
    Returns (prompt_string, regional_profile).
    """
    loader = PromptLoader()
    template = loader.load_prompt("store_caller") or ""

    region = detect_region(location)

    specs_str = json.dumps(product.get("specs") or {}, indent=2)
    alts = product.get("alternatives") or []
    alts_str = "\n".join(
        f"  {i+1}. {a['name']} (avg ₹{a.get('avg_price', 'N/A')}) – {a.get('reason', '')}"
        for i, a in enumerate(alts)
    ) or "None"

    prompt = template.replace("{product_name}", product.get("product_name", "the requested product"))
    prompt = prompt.replace("{product_specs}", specs_str)
    prompt = prompt.replace("{alternatives}", alts_str)
    prompt = prompt.replace("{location}", location)
    prompt = prompt.replace("{city}", region.get("display_name", "India"))
    prompt = prompt.replace("{regional_language}", region.get("regional_language", "hindi"))
    prompt = prompt.replace("{greeting}", region.get("greeting", "Namaste ji! Main Faff ki taraf se call kar raha hoon."))
    prompt = prompt.replace("{communication_style}", region.get("communication_style", "Speak in Hindi mixed with English."))
    prompt = prompt.replace("{thank_you}", region.get("thank_you", "Bahut dhanyavaad ji!"))
    prompt = prompt.replace("{busy_response}", region.get("busy_response", "Koi baat nahi ji, dhanyavaad!"))

    return prompt, region


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

    prompt, region = _build_store_prompt(product, location)
    results = []

    targets = stores if not test_mode else stores[:1]

    for store in targets:
        phone = test_phone if test_mode else store.get("phone_number")
        if not phone:
            logger.warning("Skipping store %s – no phone number", store["store_name"])
            continue

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
            )

            if vapi_result.get("success"):
                vapi_call_id = vapi_result.get("call", {}).get("id")
                if vapi_call_id:
                    update_store_call_vapi_id(store_call_id, vapi_call_id)
                status = "calling"
            else:
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
