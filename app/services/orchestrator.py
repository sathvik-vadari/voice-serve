"""Orchestrator LLM – classifies user queries into wakeup_alarm or order_product."""
import json
import time
import logging

from openai import AsyncOpenAI

from app.helpers.config import Config
from app.helpers.prompt_loader import PromptLoader
from app.db.tickets import log_llm_call

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
    return _client


async def classify_query(ticket_id: str, query: str) -> dict:
    """
    Call OpenAI to classify a user query.
    Returns {"category": "wakeup_alarm"|"order_product", "confidence": float, "reasoning": str}.
    Defaults to "order_product" on any failure.
    """
    loader = PromptLoader()
    system_prompt = loader.load_prompt("orchestrator") or "Classify: wakeup_alarm or order_product. Respond JSON."

    start = time.time()
    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            temperature=0.0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        latency = int((time.time() - start) * 1000)
        result = json.loads(raw)

        log_llm_call(
            ticket_id=ticket_id, step="orchestrator", model=Config.OPENAI_MODEL,
            prompt_template="orchestrator.txt",
            input_data={"query": query}, output_data=result, raw_response=raw,
            tokens_input=resp.usage.prompt_tokens if resp.usage else 0,
            tokens_output=resp.usage.completion_tokens if resp.usage else 0,
            latency_ms=latency,
        )

        if result.get("category") not in ("wakeup_alarm", "order_product"):
            result["category"] = "order_product"
        return result

    except Exception as e:
        latency = int((time.time() - start) * 1000)
        logger.exception("Orchestrator LLM failed – defaulting to order_product")
        fallback = {"category": "order_product", "confidence": 0.0, "reasoning": f"LLM error: {e}"}
        try:
            log_llm_call(
                ticket_id=ticket_id, step="orchestrator", model=Config.OPENAI_MODEL,
                prompt_template="orchestrator.txt",
                input_data={"query": query}, output_data=fallback, raw_response=str(e),
                latency_ms=latency,
            )
        except Exception:
            pass
        return fallback
