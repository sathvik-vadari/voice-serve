"""Transcript Analyzer LLM – post-call analysis of store call transcripts."""
import json
import time
import logging
from typing import Any, Optional

from openai import AsyncOpenAI

from app.helpers.config import Config
from app.helpers.prompt_loader import PromptLoader
from app.db.tickets import (
    log_llm_call,
    save_store_call_analysis,
    get_store_calls_for_ticket,
    get_product,
    get_ticket,
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
) -> dict[str, Any]:
    """
    Analyze a store call transcript with the LLM.
    Extracts structured data and verifies tool usage.
    Persists analysis to DB and checks if the ticket is complete.
    """
    loader = PromptLoader()
    system_prompt = loader.load_prompt("transcript_analyzer") or "Analyze transcript. Respond JSON."

    product = get_product(ticket_id)
    product_context = ""
    if product:
        product_context = (
            f"\nOriginal product request:\n"
            f"  Product: {product['product_name']}\n"
            f"  Category: {product.get('product_category')}\n"
            f"  Specs: {json.dumps(product.get('specs') or {})}\n"
            f"  Alternatives: {json.dumps(product.get('alternatives') or [])}\n"
        )

    tool_calls_context = ""
    if tool_calls_made:
        tool_calls_context = f"\nTool calls made during the call:\n{json.dumps(tool_calls_made, indent=2)}\n"
    else:
        tool_calls_context = "\nNo tool calls were recorded during this call.\n"

    user_message = (
        f"TRANSCRIPT:\n{transcript}\n"
        f"{tool_calls_context}"
        f"{product_context}"
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

    # Check if all calls for this ticket are done
    pending = count_pending_calls(ticket_id)
    if pending == 0:
        await _compile_final_result(ticket_id)

    return analysis


async def _compile_final_result(ticket_id: str) -> None:
    """Once all store calls are analyzed, compile the best result for the ticket."""
    calls = get_store_calls_for_ticket(ticket_id)
    product = get_product(ticket_id)
    ticket = get_ticket(ticket_id)

    if not calls:
        update_ticket_status(ticket_id, "completed", error_message="No store call data available")
        return

    # Find best option: prioritize exact match, then lowest price, then delivery availability
    scored: list[dict[str, Any]] = []
    for c in calls:
        if c["status"] != "analyzed" or not c.get("product_available"):
            continue
        match_score = {"exact": 4, "close": 3, "alternative": 2, "no_match": 0}.get(
            c.get("product_match_type") or "", 0
        )
        scored.append({**c, "_match_score": match_score})

    if not scored:
        no_result = {
            "status": "no_availability",
            "message": "None of the contacted stores have the requested product or alternatives.",
            "stores_contacted": len(calls),
            "product_requested": product["product_name"] if product else ticket.get("query") if ticket else None,
        }
        set_ticket_final_result(ticket_id, no_result)
        return

    # Sort: best match first, then lowest price, then delivery available
    scored.sort(
        key=lambda x: (
            -x["_match_score"],
            x.get("price") or 999999,
            0 if x.get("delivery_available") else 1,
        )
    )

    best = scored[0]
    all_options = []
    for s in scored:
        all_options.append({
            "store_name": s.get("store_name"),
            "phone_number": s.get("phone_number"),
            "rating": s.get("rating"),
            "matched_product": s.get("matched_product"),
            "price": s.get("price"),
            "product_match_type": s.get("product_match_type"),
            "delivery_available": s.get("delivery_available"),
            "delivery_eta": s.get("delivery_eta"),
            "delivery_mode": s.get("delivery_mode"),
            "delivery_charge": s.get("delivery_charge"),
            "notes": s.get("notes"),
        })

    final = {
        "status": "found",
        "product_requested": product["product_name"] if product else None,
        "best_option": {
            "store_name": best.get("store_name"),
            "phone_number": best.get("phone_number"),
            "matched_product": best.get("matched_product"),
            "price": best.get("price"),
            "product_match_type": best.get("product_match_type"),
            "delivery_available": best.get("delivery_available"),
            "delivery_eta": best.get("delivery_eta"),
            "delivery_mode": best.get("delivery_mode"),
            "delivery_charge": best.get("delivery_charge"),
        },
        "all_options": all_options,
        "stores_contacted": len(calls),
        "stores_with_product": len(scored),
    }

    set_ticket_final_result(ticket_id, final)
    logger.info("Ticket %s completed – best price ₹%s at %s", ticket_id, best.get("price"), best.get("store_name"))
