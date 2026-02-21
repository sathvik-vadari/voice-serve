"""VAPI webhooks: wakeup calls (/api/vapi/webhook) and store calls (/api/vapi/store-webhook)."""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Request, Response

from app.helpers.config import Config
from app.helpers.logger import setup_logger
from app.helpers.prompt_loader import PromptLoader
from app.schemas.tool_handlers import execute_tool, STORE_TOOL_HANDLERS
from app.services.vapi_client import get_wakeup_assistant_for_webhook

logger = setup_logger(__name__)

router = APIRouter(tags=["vapi-webhook"])


# ---------------------------------------------------------------------------
# Helpers shared by both webhooks
# ---------------------------------------------------------------------------

def _customer_number_from_message(body: dict) -> Optional[str]:
    msg = body.get("message") or {}
    call = msg.get("call") or body.get("call") or {}
    customer = call.get("customer") or msg.get("customer") or body.get("customer") or {}
    num = customer.get("number") if isinstance(customer, dict) else None
    num = num or call.get("customerNumber") or getattr(customer, "number", None)
    return num if isinstance(num, str) and num.strip() else None


def _vapi_call_id_from_message(body: dict) -> Optional[str]:
    msg = body.get("message") or {}
    call = msg.get("call") or body.get("call") or {}
    return call.get("id")


def _looks_like_phone(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if s in ("default_user", "default", ""):
        return False
    return s.startswith("+") or (s.isdigit() and len(s) >= 10)


def _tool_name(it: dict) -> str:
    tc = it.get("toolCall") or {}
    fn = it.get("function") or tc.get("function") or {}
    name = it.get("name") or tc.get("name")
    if not name and isinstance(fn, dict):
        name = fn.get("name")
    return name or "(unknown)"


def _tool_call_id(it: dict) -> Optional[str]:
    tc = it.get("toolCall") or {}
    return tc.get("id") or it.get("id")


def _tool_params(it: dict) -> dict:
    tc = it.get("toolCall") or {}
    fn = it.get("function") or tc.get("function") or {}
    raw = (
        it.get("parameters") or it.get("arguments")
        or tc.get("parameters") or tc.get("arguments")
        or (fn.get("parameters") if isinstance(fn, dict) else None)
        or (fn.get("arguments") if isinstance(fn, dict) else None)
        or {}
    )
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _extract_tool_call_list(body: dict) -> list:
    msg = body.get("message") or {}
    tcl = (
        msg.get("toolCallList") or msg.get("toolWithToolCallList")
        or body.get("toolCallList") or body.get("toolWithToolCallList") or []
    )
    return tcl if isinstance(tcl, list) else []


def _handle_live_transcript(body: dict, call_label: str, vapi_call_id: Optional[str]) -> bool:
    """
    Handle real-time VAPI events (transcript, conversation-update, status-update,
    speech-update). Returns True if the event was handled and the caller should
    return early.
    """
    msg = body.get("message") or {}
    msg_type = msg.get("type") or ""
    tag = f"[{call_label}:{vapi_call_id or '?'}]"

    if msg_type == "transcript":
        role = msg.get("role") or "?"
        text = msg.get("transcript") or ""
        ttype = msg.get("transcriptType") or "partial"
        if ttype == "final":
            logger.info("%s 🎙  %s: %s", tag, role.upper(), text)
        else:
            logger.debug("%s 🎙  %s (partial): %s", tag, role.upper(), text)
        return True

    if msg_type == "conversation-update":
        conversation = msg.get("conversation") or []
        if conversation:
            last = conversation[-1]
            role = last.get("role") or "?"
            content = last.get("content") or ""
            logger.info("%s 💬 conversation [%d msgs] latest %s: %s",
                        tag, len(conversation), role.upper(), content[:300])
        return True

    if msg_type == "status-update":
        status = msg.get("status") or "(unknown)"
        logger.info("%s 📞 status -> %s", tag, status)
        return True

    if msg_type == "speech-update":
        status = msg.get("status") or "(unknown)"
        role = msg.get("role") or "?"
        logger.info("%s 🔊 %s speech %s", tag, role, status)
        return True

    return False


# ---------------------------------------------------------------------------
# Original wakeup webhook (unchanged behavior)
# ---------------------------------------------------------------------------

@router.post("/api/vapi/webhook")
async def vapi_webhook(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception as e:
        logger.warning("VAPI webhook invalid JSON: %s", e)
        return Response(status_code=400, content=b"Invalid JSON")

    msg = body.get("message") or {}
    msg_type = msg.get("type") or "(unknown)"
    vapi_call_id = _vapi_call_id_from_message(body)

    # ---- live transcript / status / speech events ----
    if _handle_live_transcript(body, "wakeup", vapi_call_id):
        return Response(status_code=200, content=b"{}")

    # ---- end-of-call-report: save transcript + finalize ticket ----
    if msg_type == "end-of-call-report":
        ended_reason = msg.get("endedReason") or body.get("endedReason") or "(none)"
        transcript = (msg.get("transcript") or msg.get("artifact", {}).get("transcript") or "").strip()
        logger.info("VAPI wakeup call ended: vapi_call_id=%s reason=%s", vapi_call_id, ended_reason)
        if transcript:
            logger.info("Transcript: %s", transcript[:2000])

        if vapi_call_id:
            try:
                from app.db.tickets import get_ticket_by_vapi_call_id, save_ticket_transcript
                ticket = get_ticket_by_vapi_call_id(vapi_call_id)
                if ticket:
                    save_ticket_transcript(ticket["ticket_id"], transcript, ended_reason)
                    logger.info("Wakeup transcript saved for ticket %s", ticket["ticket_id"])
            except Exception:
                logger.exception("Failed to save wakeup transcript for vapi_call_id=%s", vapi_call_id)

        return Response(status_code=200, content=b"{}")

    # ---- assistant-request ----
    if msg_type == "assistant-request":
        server_url = Config.VAPI_SERVER_URL or str(request.base_url).rstrip("/")
        prompt_loader = PromptLoader()
        system_prompt = prompt_loader.get_default_prompt()
        assistant = get_wakeup_assistant_for_webhook(server_url, system_prompt)
        return Response(content=json.dumps({"assistant": assistant}), media_type="application/json")

    # ---- tool-calls: execute + log to ticket ----
    if msg_type == "tool-calls":
        customer_number = _customer_number_from_message(body)
        tool_call_list = _extract_tool_call_list(body)

        logger.info("VAPI wakeup tool-calls: %d items, vapi_call_id=%s", len(tool_call_list), vapi_call_id)
        results = []
        for item in tool_call_list:
            name = _tool_name(item)
            tcid = _tool_call_id(item)
            params = _tool_params(item)

            if not name or name == "(unknown)":
                continue

            if customer_number and "user_id" not in params:
                params["user_id"] = customer_number

            if name == "schedule_wakeup_call":
                user_id = params.get("user_id") or customer_number
                if not _looks_like_phone(str(user_id or "")):
                    result = {"success": False, "error": "No valid phone number for callback."}
                else:
                    result = await execute_tool(name, json.dumps(params))
            else:
                result = await execute_tool(name, json.dumps(params))

            logger.info("Tool %s -> %s", name, result.get("message") or result.get("error") or "ok")
            results.append({
                "name": name,
                "toolCallId": tcid,
                "result": json.dumps(result) if not isinstance(result, str) else result,
            })

            # Log tool call to ticket if we can find it
            if vapi_call_id:
                try:
                    from app.db.tickets import get_ticket_by_vapi_call_id, append_ticket_tool_call
                    ticket = get_ticket_by_vapi_call_id(vapi_call_id)
                    if ticket:
                        append_ticket_tool_call(ticket["ticket_id"], {
                            "tool": name, "params": params, "result": result,
                        })
                except Exception:
                    logger.exception("Failed to log wakeup tool call to ticket")

        return Response(content=json.dumps({"results": results}), media_type="application/json")

    return Response(status_code=200, content=b"{}")


# ---------------------------------------------------------------------------
# Store inquiry webhook (new)
# ---------------------------------------------------------------------------

@router.post("/api/vapi/store-webhook")
async def vapi_store_webhook(request: Request) -> Response:
    """Handle VAPI webhooks for store inquiry calls."""
    try:
        body = await request.json()
    except Exception as e:
        logger.warning("Store webhook invalid JSON: %s", e)
        return Response(status_code=400, content=b"Invalid JSON")

    msg = body.get("message") or {}
    msg_type = msg.get("type") or "(unknown)"
    vapi_call_id = _vapi_call_id_from_message(body)

    # ---- live transcript / status / speech events ----
    if _handle_live_transcript(body, "store", vapi_call_id):
        return Response(status_code=200, content=b"{}")

    # ---- end-of-call-report: trigger transcript analysis ----
    if msg_type == "end-of-call-report":
        artifact = msg.get("artifact") or {}
        transcript = (msg.get("transcript") or artifact.get("transcript") or "").strip()
        transcript_messages = artifact.get("messages") or []
        ended_reason = msg.get("endedReason") or body.get("endedReason") or "(none)"
        logger.info("Store call ended: vapi_call_id=%s reason=%s transcript_len=%d messages=%d",
                     vapi_call_id, ended_reason, len(transcript), len(transcript_messages))

        if vapi_call_id:
            if transcript or transcript_messages:
                asyncio.create_task(_handle_store_transcript(
                    vapi_call_id, transcript, ended_reason, transcript_messages,
                ))
            else:
                asyncio.create_task(_handle_store_no_transcript(vapi_call_id, ended_reason))

        return Response(status_code=200, content=b"{}")

    # ---- tool-calls: execute store tools ----
    if msg_type == "tool-calls":
        tool_call_list = _extract_tool_call_list(body)
        logger.info("Store tool-calls: %d items, vapi_call_id=%s", len(tool_call_list), vapi_call_id)

        # Accumulate tool calls for later analysis
        accumulated: list[dict] = []
        results = []
        for item in tool_call_list:
            name = _tool_name(item)
            tcid = _tool_call_id(item)
            params = _tool_params(item)

            if not name or name == "(unknown)" or name not in STORE_TOOL_HANDLERS:
                continue

            extra = {"_vapi_call_id": vapi_call_id} if vapi_call_id else {}
            result = await execute_tool(name, json.dumps(params), extra_context=extra)

            accumulated.append({"tool": name, "params": params, "result": result})
            results.append({
                "name": name,
                "toolCallId": tcid,
                "result": json.dumps(result) if not isinstance(result, str) else result,
            })

        # Persist raw tool calls on the store_call record
        if vapi_call_id and accumulated:
            try:
                from app.db.tickets import save_store_call_tool_calls
                save_store_call_tool_calls(vapi_call_id, accumulated)
            except Exception:
                logger.exception("Failed to persist store tool calls")

        return Response(content=json.dumps({"results": results}), media_type="application/json")

    return Response(status_code=200, content=b"{}")


async def _handle_store_transcript(
    vapi_call_id: str,
    transcript: str,
    ended_reason: str = "",
    transcript_messages: list[dict] | None = None,
) -> None:
    """Background task: save transcript and run the transcript analyzer LLM."""
    try:
        from app.db.tickets import save_store_call_transcript, get_store_call_by_vapi_id
        from app.services.transcript_analyzer import analyze_transcript

        call_id = save_store_call_transcript(vapi_call_id, transcript, transcript_messages)
        if not call_id:
            logger.warning("No store_call found for vapi_call_id=%s", vapi_call_id)
            return

        sc = get_store_call_by_vapi_id(vapi_call_id)
        if not sc:
            return

        from app.db.connection import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tool_calls_raw FROM store_calls WHERE id = %s", (call_id,))
                row = cur.fetchone()
        tool_calls_made = row[0] if row and row[0] else []

        await analyze_transcript(
            ticket_id=sc["ticket_id"],
            store_call_id=call_id,
            transcript=transcript,
            tool_calls_made=tool_calls_made,
            ended_reason=ended_reason,
        )
        logger.info("Transcript analysis complete for store_call %s (ticket %s)", call_id, sc["ticket_id"])

    except Exception:
        logger.exception("Store transcript handling failed for vapi_call_id=%s", vapi_call_id)


_RETRYABLE_ENDED_REASONS = frozenset({
    "customer-busy",
    "customer-did-not-answer",
    "customer-did-not-pick-up",
})


async def _handle_store_no_transcript(vapi_call_id: str, ended_reason: str) -> None:
    """Handle calls that ended without a transcript (no answer, failed, etc.).

    If the vendor simply didn't pick up and we haven't exhausted retries,
    schedule a callback after STORE_CALL_RETRY_DELAY_SECONDS (default 2 min).
    """
    try:
        from app.db.tickets import (
            get_store_call_by_vapi_id, update_store_call_status,
            save_store_call_analysis, count_pending_calls,
            get_store_call_retry_count,
        )
        from app.services.transcript_analyzer import _compile_final_result

        sc = get_store_call_by_vapi_id(vapi_call_id)
        if not sc:
            logger.warning("No store_call found for vapi_call_id=%s (no transcript)", vapi_call_id)
            return

        retry_count = get_store_call_retry_count(sc["id"])
        max_retries = Config.STORE_CALL_MAX_RETRIES

        if ended_reason in _RETRYABLE_ENDED_REASONS and retry_count < max_retries:
            attempt = retry_count + 1
            delay = Config.STORE_CALL_RETRY_DELAY_SECONDS
            logger.info(
                "Store call %s (vapi=%s) vendor didn't answer (%s). "
                "Scheduling retry %d/%d in %ds",
                sc["id"], vapi_call_id, ended_reason, attempt, max_retries, delay,
            )
            update_store_call_status(sc["id"], "retry_scheduled")
            asyncio.create_task(_retry_store_call(sc, delay))
            return

        failure_reasons = {
            "customer-busy": "Store was busy / line engaged",
            "customer-did-not-answer": "Store did not answer the call",
            "customer-did-not-pick-up": "Store did not pick up",
            "assistant-error": "Call assistant encountered an error",
            "phone-call-provider-closed-websocket": "Call dropped by provider",
            "silence-timed-out": "No response — silence timeout",
            "voicemail": "Reached voicemail",
        }
        note = failure_reasons.get(ended_reason, f"Call ended: {ended_reason}")
        if ended_reason in _RETRYABLE_ENDED_REASONS and retry_count >= max_retries:
            note += f" (after {retry_count + 1} attempts)"

        save_store_call_analysis(sc["id"], {
            "product_available": None,
            "matched_product": None,
            "price": None,
            "delivery_available": None,
            "delivery_eta": None,
            "delivery_mode": None,
            "delivery_charge": None,
            "product_match_type": "no_data",
            "notes": note,
            "data_quality_score": 0.0,
            "ended_reason": ended_reason,
            "call_connected": False,
        })

        logger.info("Store call %s (vapi=%s) ended without transcript: %s",
                     sc["id"], vapi_call_id, note)

        pending = count_pending_calls(sc["ticket_id"])
        if pending == 0:
            await _compile_final_result(sc["ticket_id"])

    except Exception:
        logger.exception("Failed handling no-transcript call for vapi_call_id=%s", vapi_call_id)


async def _retry_store_call(sc: dict, delay_seconds: int) -> None:
    """Wait `delay_seconds` then re-initiate the VAPI call to the same store."""
    try:
        await asyncio.sleep(delay_seconds)

        from app.db.tickets import (
            get_store_by_id, get_product, get_ticket,
            reset_store_call_for_retry, update_store_call_status,
            get_store_call_retry_count, log_tool_call,
        )
        from app.services.store_caller import _build_store_prompt
        from app.services.vapi_client import create_store_phone_call
        from app.helpers.regional import detect_region

        store = get_store_by_id(sc["store_id"])
        if not store:
            logger.error("Retry aborted: store %s not found", sc["store_id"])
            update_store_call_status(sc["id"], "failed")
            return

        ticket = get_ticket(sc["ticket_id"])
        if not ticket:
            logger.error("Retry aborted: ticket %s not found", sc["ticket_id"])
            update_store_call_status(sc["id"], "failed")
            return

        product = get_product(sc["ticket_id"])
        if not product:
            logger.error("Retry aborted: no product for ticket %s", sc["ticket_id"])
            update_store_call_status(sc["id"], "failed")
            return

        phone = store.get("phone_number")
        if not phone:
            logger.error("Retry aborted: no phone for store %s", store.get("store_name"))
            update_store_call_status(sc["id"], "failed")
            return

        location = ticket.get("location", "")
        retry_num = get_store_call_retry_count(sc["id"]) + 1

        prompt, region, first_message = _build_store_prompt(
            product, location, store["store_name"],
        )

        logger.info(
            "Retrying store call %s (attempt %d) to %s (%s)",
            sc["id"], retry_num + 1, store["store_name"], phone,
        )

        vapi_result = await create_store_phone_call(
            customer_number=phone,
            system_prompt=prompt,
            ticket_id=sc["ticket_id"],
            store_call_id=sc["id"],
            region=region,
            first_message=first_message,
        )

        if vapi_result.get("success"):
            new_vapi_call_id = vapi_result.get("call", {}).get("id")
            if new_vapi_call_id:
                reset_store_call_for_retry(sc["id"], new_vapi_call_id)
                logger.info(
                    "Store call %s retry successful, new vapi_call_id=%s",
                    sc["id"], new_vapi_call_id,
                )
            else:
                update_store_call_status(sc["id"], "failed")
        else:
            logger.error(
                "Store call %s retry VAPI call failed: %s",
                sc["id"], vapi_result.get("error"),
            )
            update_store_call_status(sc["id"], "failed")

        log_tool_call(
            sc["ticket_id"], "vapi_retry_store_call",
            {"store": store["store_name"], "phone": phone, "retry_attempt": retry_num},
            vapi_result,
            status="success" if vapi_result.get("success") else "error",
            error_message=vapi_result.get("error"),
            store_call_id=sc["id"],
        )

    except Exception:
        logger.exception("Retry failed for store_call %s", sc.get("id"))
