"""VAPI webhook: assistant-request and tool-calls for wake-up call phone flows."""
import json
from typing import Optional

from fastapi import APIRouter, Request, Response

from app.helpers.config import Config
from app.helpers.logger import setup_logger
from app.helpers.prompt_loader import PromptLoader
from app.schemas.tool_handlers import execute_tool
from app.services.vapi_client import get_wakeup_assistant_for_webhook

logger = setup_logger(__name__)

router = APIRouter(tags=["vapi-webhook"])


def _customer_number_from_message(body: dict) -> Optional[str]:
    """Extract customer phone number from VAPI webhook message."""
    msg = body.get("message") or {}
    call = msg.get("call") or body.get("call") or {}
    customer = call.get("customer") or msg.get("customer") or body.get("customer") or {}
    num = customer.get("number") if isinstance(customer, dict) else None
    num = num or call.get("customerNumber") or getattr(customer, "number", None)
    return num if isinstance(num, str) and num.strip() else None


def _looks_like_phone(s: str) -> bool:
    """True if string looks like a phone number (for callback)."""
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if s in ("default_user", "default", ""):
        return False
    return s.startswith("+") or (s.isdigit() and len(s) >= 10)


@router.post("/api/vapi/webhook")
async def vapi_webhook(request: Request) -> Response:
    """
    VAPI sends POST here for assistant-request and tool-calls.
    Set this URL as your Phone Number's or Assistant's Server URL in VAPI dashboard.
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.warning("VAPI webhook invalid JSON: %s", e)
        return Response(status_code=400, content=b"Invalid JSON")

    msg = body.get("message") or {}
    msg_type = msg.get("type") or "(unknown)"

    if msg_type == "end-of-call-report":
        ended_reason = msg.get("endedReason") or body.get("endedReason") or "(none)"
        transcript = (msg.get("transcript") or msg.get("artifact", {}).get("transcript") or "").strip()
        logger.info("VAPI call ended: reason=%s", ended_reason)
        if transcript:
            logger.info("Transcript: %s", transcript[:2000] if len(transcript) > 2000 else transcript)

    if msg_type == "assistant-request":
        server_url = Config.VAPI_SERVER_URL or str(request.base_url).rstrip("/")
        prompt_loader = PromptLoader()
        system_prompt = prompt_loader.get_default_prompt()
        assistant = get_wakeup_assistant_for_webhook(server_url, system_prompt)
        return Response(
            content=json.dumps({"assistant": assistant}),
            media_type="application/json",
        )

    if msg_type == "tool-calls":
        customer_number = _customer_number_from_message(body)
        tool_call_list = (
            msg.get("toolCallList")
            or msg.get("toolWithToolCallList")
            or body.get("toolCallList")
            or body.get("toolWithToolCallList")
            or []
        )
        if not isinstance(tool_call_list, list):
            tool_call_list = []

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
                it.get("parameters")
                or it.get("arguments")
                or tc.get("parameters")
                or tc.get("arguments")
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

        tool_names = [_tool_name(it) for it in tool_call_list]
        logger.info("VAPI tool-calls: %d items, customer_number=%s, tools=%s", len(tool_call_list), customer_number, tool_names)
        if not tool_call_list:
            logger.warning("VAPI tool-calls: no items in toolCallList/toolWithToolCallList. message keys: %s", list(msg.keys()))
        elif "(unknown)" in tool_names:
            logger.warning("VAPI tool-calls: first item keys: %s", list((tool_call_list[0] or {}).keys()))

        results = []
        for item in tool_call_list:
            name = _tool_name(item)
            tool_call_id = _tool_call_id(item)
            params = _tool_params(item)

            if not name or name == "(unknown)":
                continue
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except json.JSONDecodeError:
                    params = {}

            if customer_number and "user_id" not in params:
                params = {**params, "user_id": customer_number}

            if name == "schedule_wakeup_call":
                user_id = params.get("user_id") or customer_number
                if not _looks_like_phone(str(user_id or "")):
                    logger.error("schedule_wakeup_call: no valid phone number (got %r). Cannot callback.", user_id)
                    result = {"success": False, "error": "I don't have your phone number to call you back. Please try again."}
                else:
                    args_str = json.dumps(params)
                    try:
                        result = await execute_tool(name, args_str)
                    except Exception as e:
                        logger.exception("Tool execution failed: %s", name)
                        result = {"error": str(e)}
            else:
                args_str = json.dumps(params)
                try:
                    result = await execute_tool(name, args_str)
                except Exception as e:
                    logger.exception("Tool execution failed: %s", name)
                    result = {"error": str(e)}

            logger.info("Tool %s(%s) -> %s", name, params, result.get("message") or result.get("error") or "ok")
            results.append({
                "name": name,
                "toolCallId": tool_call_id,
                "result": json.dumps(result) if not isinstance(result, str) else result,
            })

        return Response(
            content=json.dumps({"results": results}),
            media_type="application/json",
        )

    return Response(status_code=200, content=b"{}")
