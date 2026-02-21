"""Generate a user-facing summary message from completed store call options."""
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from openai import AsyncOpenAI

from app.helpers.config import Config
from app.helpers.prompt_loader import PromptLoader
from app.db.tickets import (
    get_ticket,
    get_product,
    get_store_calls_for_ticket,
    log_llm_call,
)

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
    return _client


def _format_transcript(option: dict[str, Any]) -> str:
    """
    Prefer structured transcript_json (role-labeled messages) over the raw text blob.
    VAPI's artifact.messages looks like:
      [{"role": "bot", "message": "...", "time": ...}, {"role": "user", "message": "...", ...}]
    """
    messages = option.get("transcript_json")
    if messages and isinstance(messages, list):
        lines = []
        for m in messages:
            role = (m.get("role") or "unknown").upper()
            if role in ("BOT", "ASSISTANT"):
                role = "CALLER"
            elif role == "USER":
                role = "STORE"
            content = m.get("message") or m.get("content") or ""
            if content.strip():
                lines.append(f"    {role}: {content.strip()}")
        if lines:
            return "\n".join(lines)

    raw = option.get("transcript")
    if raw and isinstance(raw, str) and raw.strip():
        return "    " + raw.strip().replace("\n", "\n    ")

    return "    (no transcript available)"


def _build_options(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to successful calls and shape them into clean option dicts."""
    options = []
    for c in calls:
        if c.get("status") != "analyzed" or not c.get("product_available"):
            continue

        analysis = c.get("call_analysis") or {}
        option: dict[str, Any] = {
            "store_name": c.get("store_name"),
            "address": c.get("address"),
            "phone_number": c.get("phone_number"),
            "rating": c.get("rating"),
            "matched_product": c.get("matched_product"),
            "price": c.get("price"),
            "product_match_type": c.get("product_match_type"),
            "delivery_available": c.get("delivery_available"),
            "delivery_eta": c.get("delivery_eta"),
            "delivery_mode": c.get("delivery_mode"),
            "delivery_charge": c.get("delivery_charge"),
            "specs_gathered": analysis.get("specs_gathered"),
            "specs_match_score": analysis.get("specs_match_score"),
            "call_summary": analysis.get("call_summary", c.get("notes")),
            "notes": c.get("notes"),
            "transcript": c.get("transcript"),
            "transcript_json": c.get("transcript_json"),
        }
        options.append(option)

    match_type_weight = {"exact": 4, "close": 3, "alternative": 2, "no_match": 0, "no_data": 0}
    options.sort(
        key=lambda o: (
            -(match_type_weight.get(o.get("product_match_type") or "", 0) * 3
              + float(o.get("specs_match_score") or 0) * 2),
            o.get("price") or 999999,
        )
    )
    return options


async def generate_options_summary(ticket_id: str) -> dict[str, Any]:
    """
    Build the full options payload for a completed ticket.
    Returns structured options + an LLM-generated user-facing message.
    """
    ticket = get_ticket(ticket_id)
    if not ticket:
        return {"error": "Ticket not found"}

    if ticket["status"] not in ("completed",):
        return {
            "error": "Ticket is not completed yet",
            "status": ticket["status"],
            "ticket_id": ticket_id,
        }

    product = get_product(ticket_id)
    all_calls = get_store_calls_for_ticket(ticket_id)
    options = _build_options(all_calls)

    total_calls = len(all_calls)
    connected = sum(
        1 for c in all_calls
        if c.get("status") == "analyzed"
        and (c.get("call_analysis") or {}).get("call_connected", False)
    )

    product_name = product["product_name"] if product else ticket.get("query", "unknown")
    customer_specs = product.get("specs") or {} if product else {}

    result: dict[str, Any] = {
        "ticket_id": ticket_id,
        "product_requested": product_name,
        "customer_specs": customer_specs,
        "avg_price_online": product.get("avg_price_online") if product else None,
        "stores_contacted": total_calls,
        "calls_connected": connected,
        "options_found": len(options),
        "options": options,
    }

    if not options:
        result["message"] = (
            f"We called {total_calls} stores for '{product_name}' but unfortunately "
            f"none of them had it available right now. "
            f"Would you like us to try different stores or broaden the search?"
        )
        result["quick_verdict"] = "No stores had the product available."
        return result

    message_data = await _generate_message(ticket_id, product_name, customer_specs, options, total_calls, connected)
    result["message"] = message_data.get("message", "")
    result["quick_verdict"] = message_data.get("quick_verdict", "")

    for opt in result["options"]:
        opt.pop("transcript_json", None)
        opt.pop("transcript", None)
        
    return result


async def _generate_message(
    ticket_id: str,
    product_name: str,
    customer_specs: dict,
    options: list[dict],
    total_calls: int,
    connected: int,
) -> dict[str, str]:
    """Call the LLM to produce a user-friendly options message."""
    loader = PromptLoader()
    system_prompt = loader.load_prompt("options_summary") or "Summarize the store options for the user."

    specs_str = ""
    if customer_specs:
        specs_str = "\n".join(f"  - {k}: {v}" for k, v in customer_specs.items())

    options_text = []
    for idx, opt in enumerate(options, 1):
        parts = [f"Store: {opt['store_name']}"]
        if opt.get("address"):
            parts.append(f"  Address: {opt['address']}")
        if opt.get("phone_number"):
            parts.append(f"  Phone: {opt['phone_number']}")
        if opt.get("rating"):
            parts.append(f"  Rating: {opt['rating']}/5")
        if opt.get("matched_product"):
            parts.append(f"  Product found: {opt['matched_product']}")
        if opt.get("price") is not None:
            parts.append(f"  Price: ₹{opt['price']}")
        if opt.get("product_match_type"):
            parts.append(f"  Match type: {opt['product_match_type']}")
        if opt.get("delivery_available") is not None:
            delivery = "Yes" if opt["delivery_available"] else "No"
            if opt.get("delivery_eta"):
                delivery += f", ETA: {opt['delivery_eta']}"
            if opt.get("delivery_mode"):
                delivery += f" ({opt['delivery_mode']})"
            if opt.get("delivery_charge") is not None:
                delivery += f", Charge: ₹{opt['delivery_charge']}" if opt["delivery_charge"] else ", Free"
            parts.append(f"  Delivery: {delivery}")
        if opt.get("specs_gathered"):
            relevant = {k: v for k, v in opt["specs_gathered"].items() if v}
            if relevant:
                parts.append(f"  Specs: {json.dumps(relevant)}")
        if opt.get("call_summary"):
            parts.append(f"  Summary: {opt['call_summary']}")
        if opt.get("notes"):
            parts.append(f"  Notes: {opt['notes']}")
        parts.append(f"  Transcript:\n{_format_transcript(opt)}")
        options_text.append(f"--- Option {idx} ---\n" + "\n".join(parts))

    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    current_datetime = ist_now.strftime("%A, %d %B %Y, %I:%M %p IST")

    user_message = (
        f"CURRENT DATE & TIME: {current_datetime}\n\n"
        f"PRODUCT REQUEST: {product_name}\n"
        f"CUSTOMER SPECS:\n{specs_str or '  (none specified)'}\n\n"
        f"STORES CONTACTED: {total_calls}\n"
        f"CALLS CONNECTED: {connected}\n"
        f"OPTIONS WITH PRODUCT AVAILABLE: {len(options)}\n\n"
        + "\n\n".join(options_text)
    )

    client = _get_client()
    start = time.time()

    resp = await client.chat.completions.create(
        model=Config.OPENAI_MODEL,
        temperature=0.7,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content or "{}"
    latency = int((time.time() - start) * 1000)
    result = json.loads(raw)

    log_llm_call(
        ticket_id=ticket_id,
        step="options_summary",
        model=Config.OPENAI_MODEL,
        prompt_template="options_summary.txt",
        input_data={"options_count": len(options), "total_calls": total_calls},
        output_data=result,
        raw_response=raw,
        tokens_input=resp.usage.prompt_tokens if resp.usage else 0,
        tokens_output=resp.usage.completion_tokens if resp.usage else 0,
        latency_ms=latency,
    )

    return result
