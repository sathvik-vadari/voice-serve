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

    # ---- end-of-call-report: trigger transcript analysis ----
    if msg_type == "end-of-call-report":
        transcript = (msg.get("transcript") or msg.get("artifact", {}).get("transcript") or "").strip()
        ended_reason = msg.get("endedReason") or body.get("endedReason") or "(none)"
        logger.info("Store call ended: vapi_call_id=%s reason=%s", vapi_call_id, ended_reason)

        if vapi_call_id and transcript:
            asyncio.create_task(_handle_store_transcript(vapi_call_id, transcript))

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


async def _handle_store_transcript(vapi_call_id: str, transcript: str) -> None:
    """Background task: save transcript and run the transcript analyzer LLM."""
    try:
        from app.db.tickets import save_store_call_transcript, get_store_call_by_vapi_id
        from app.services.transcript_analyzer import analyze_transcript

        call_id = save_store_call_transcript(vapi_call_id, transcript)
        if not call_id:
            logger.warning("No store_call found for vapi_call_id=%s", vapi_call_id)
            return

        sc = get_store_call_by_vapi_id(vapi_call_id)
        if not sc:
            return

        # Fetch tool calls made during this call
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
        )
        logger.info("Transcript analysis complete for store_call %s (ticket %s)", call_id, sc["ticket_id"])

    except Exception:
        logger.exception("Store transcript handling failed for vapi_call_id=%s", vapi_call_id)
