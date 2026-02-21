"""Transcript Analyzer LLM – post-call analysis of store call transcripts."""
import json
import time
import logging
from typing import Any

from openai import AsyncOpenAI

from app.helpers.config import Config
from app.helpers.prompt_loader import PromptLoader
from app.db.tickets import (
    log_llm_call,
    save_store_call_analysis,
    get_store_calls_for_ticket,
    get_product,
    get_ticket,
    get_web_deals,
    count_pending_calls,
    set_ticket_final_result,
    update_ticket_status,
)

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
    return _client


async def analyze_transcript(
    ticket_id: str,
    store_call_id: int,
    transcript: str,
    tool_calls_made: list[dict] | None = None,
    ended_reason: str = "",
) -> dict[str, Any]:
    """
    Analyze a store call transcript with the LLM.
    Extracts structured data, verifies tool usage, and scores spec match.
    Persists analysis to DB and checks if the ticket is complete.
    """
    loader = PromptLoader()
    system_prompt = loader.load_prompt("transcript_analyzer") or "Analyze transcript. Respond JSON."

    product = get_product(ticket_id)
    product_context = ""
    if product:
        specs = product.get("specs") or {}
        specs_lines = "\n".join(f"    - {k}: {v}" for k, v in specs.items()) if specs else "    (none)"
        alts = product.get("alternatives") or []
        alts_lines = "\n".join(f"    - {a.get('name', '?')}" for a in alts) if alts else "    (none)"
        product_context = (
            f"\nOriginal product request (what the customer wants):\n"
            f"  Product: {product['product_name']}\n"
            f"  Category: {product.get('product_category')}\n"
            f"  Customer's required specs:\n{specs_lines}\n"
            f"  Alternatives to check if unavailable:\n{alts_lines}\n"
        )

    tool_calls_context = ""
    if tool_calls_made:
        tool_calls_context = f"\nTool calls made during the call:\n{json.dumps(tool_calls_made, indent=2)}\n"
    else:
        tool_calls_context = "\nNo tool calls were recorded during this call.\n"

    ended_context = ""
    if ended_reason:
        ended_context = f"\nCall ended reason: {ended_reason}\n"

    user_message = (
        f"TRANSCRIPT:\n{transcript}\n"
        f"{tool_calls_context}"
        f"{product_context}"
        f"{ended_context}"
    )

    start = time.time()
    client = _get_client()

    resp = await client.chat.completions.create(
        model=Config.OPENAI_MODEL,
        temperature=0.0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    latency = int((time.time() - start) * 1000)
    analysis = json.loads(raw)

    log_llm_call(
        ticket_id=ticket_id, step="transcript_analyzer", model=Config.OPENAI_MODEL,
        prompt_template="transcript_analyzer.txt",
        input_data={"store_call_id": store_call_id, "transcript_length": len(transcript)},
        output_data=analysis, raw_response=raw,
        tokens_input=resp.usage.prompt_tokens if resp.usage else 0,
        tokens_output=resp.usage.completion_tokens if resp.usage else 0,
        latency_ms=latency,
    )

    save_store_call_analysis(store_call_id, analysis)

    pending = count_pending_calls(ticket_id)
    if pending == 0:
        await _compile_final_result(ticket_id)

    return analysis


async def _compile_final_result(ticket_id: str) -> None:
    """Once all store calls are done, compile the best result with clear recommendation."""
    calls = get_store_calls_for_ticket(ticket_id)
    product = get_product(ticket_id)
    ticket = get_ticket(ticket_id)

    if not calls:
        update_ticket_status(ticket_id, "completed", error_message="No store call data available")
        return

    product_name = product["product_name"] if product else (ticket.get("query") if ticket else "unknown")
    customer_specs = product.get("specs") or {} if product else {}

    connected_calls = []
    failed_calls = []

    for c in calls:
        analysis = c.get("call_analysis") or {}
        was_connected = analysis.get("call_connected", c["status"] == "analyzed")

        entry = {
            **c,
            "_connected": was_connected,
            "_summary": analysis.get("call_summary", c.get("notes") or ""),
            "_specs_gathered": analysis.get("specs_gathered") or {},
            "_specs_match": analysis.get("specs_match_score", 0.0),
            "_data_quality": analysis.get("data_quality_score", 0.0),
        }

        if was_connected and c.get("product_available") is not None:
            connected_calls.append(entry)
        else:
            failed_calls.append(entry)

    available_calls = [c for c in connected_calls if c.get("product_available")]

    if not available_calls:
        no_result = {
            "status": "no_availability",
            "product_requested": product_name,
            "customer_specs": customer_specs,
            "message": "None of the contacted stores have the requested product.",
            "stores_contacted": len(calls),
            "calls_connected": len(connected_calls),
            "calls_failed": len(failed_calls),
            "call_details": [
                {
                    "store_name": c.get("store_name"),
                    "phone_number": c.get("phone_number"),
                    "status": "connected" if c["_connected"] else "not_connected",
                    "summary": c["_summary"],
                    "product_available": c.get("product_available"),
                    "notes": c.get("notes"),
                }
                for c in connected_calls + failed_calls
            ],
        }
        web_deals = get_web_deals(ticket_id)
        if web_deals and web_deals.get("deals"):
            no_result["web_deals"] = web_deals
            no_result["message"] = (
                "None of the local stores had the product, "
                "but we found online deals for you!"
            )
        set_ticket_final_result(ticket_id, no_result)
        return

    scored = []
    for c in available_calls:
        match_score = {"exact": 4, "close": 3, "alternative": 2, "no_match": 0, "no_data": 0}.get(
            c.get("product_match_type") or "", 0
        )
        specs_match = float(c.get("_specs_match") or 0)
        data_quality = float(c.get("_data_quality") or 0)
        composite = (match_score * 3) + (specs_match * 2) + data_quality
        scored.append({**c, "_composite_score": composite})

    scored.sort(key=lambda x: (-x["_composite_score"], x.get("price") or 999999))

    best = scored[0]
    all_options = []
    for idx, s in enumerate(scored):
        analysis = s.get("call_analysis") or {}
        option = {
            "rank": idx + 1,
            "store_name": s.get("store_name"),
            "phone_number": s.get("phone_number"),
            "rating": s.get("rating"),
            "matched_product": s.get("matched_product"),
            "price": s.get("price"),
            "product_match_type": s.get("product_match_type"),
            "specs_gathered": s.get("_specs_gathered"),
            "specs_match_score": s.get("_specs_match"),
            "delivery_available": s.get("delivery_available"),
            "delivery_eta": s.get("delivery_eta"),
            "delivery_mode": s.get("delivery_mode"),
            "delivery_charge": s.get("delivery_charge"),
            "data_quality_score": s.get("_data_quality"),
            "call_summary": s.get("_summary"),
            "notes": s.get("notes"),
        }
        all_options.append(option)

    recommendation_parts = []
    best_product = best.get("matched_product") or product_name
    if best.get("price"):
        recommendation_parts.append(
            f"Best match: {best.get('store_name')} has {best_product} at ₹{best['price']}"
        )
    else:
        recommendation_parts.append(
            f"Best match: {best.get('store_name')} has {best_product}"
        )
    if best.get("_specs_gathered"):
        specs_info = ", ".join(f"{k}: {v}" for k, v in best["_specs_gathered"].items() if v)
        if specs_info:
            recommendation_parts.append(f"Specs: {specs_info}")
    if best.get("delivery_available"):
        delivery_msg = "Delivers to your area"
        if best.get("delivery_eta"):
            delivery_msg += f" in {best['delivery_eta']}"
        if best.get("delivery_charge"):
            delivery_msg += f" (₹{best['delivery_charge']} charge)"
        elif best.get("delivery_charge") == 0:
            delivery_msg += " (free delivery)"
        recommendation_parts.append(delivery_msg)

    final = {
        "status": "found",
        "product_requested": product_name,
        "customer_specs": customer_specs,
        "recommendation": " | ".join(recommendation_parts),
        "best_option": {
            "store_name": best.get("store_name"),
            "phone_number": best.get("phone_number"),
            "rating": best.get("rating"),
            "matched_product": best.get("matched_product"),
            "price": best.get("price"),
            "product_match_type": best.get("product_match_type"),
            "specs_gathered": best.get("_specs_gathered"),
            "specs_match_score": best.get("_specs_match"),
            "delivery_available": best.get("delivery_available"),
            "delivery_eta": best.get("delivery_eta"),
            "delivery_mode": best.get("delivery_mode"),
            "delivery_charge": best.get("delivery_charge"),
            "call_summary": best.get("_summary"),
        },
        "all_options": all_options,
        "stores_contacted": len(calls),
        "calls_connected": len(connected_calls),
        "calls_failed": len(failed_calls),
        "stores_with_product": len(available_calls),
    }

    web_deals = get_web_deals(ticket_id)
    if web_deals and web_deals.get("deals"):
        final["web_deals"] = web_deals

    set_ticket_final_result(ticket_id, final)
    logger.info(
        "Ticket %s completed – best: %s (₹%s, spec_match=%.1f, quality=%.1f)",
        ticket_id, best.get("store_name"), best.get("price"),
        best.get("_specs_match", 0), best.get("_data_quality", 0),
    )
