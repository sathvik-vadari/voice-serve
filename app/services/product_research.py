"""Product Research LLM – identifies product details, specs, alternatives, and store search term."""
import json
import time
import logging

from openai import AsyncOpenAI

from app.helpers.config import Config
from app.helpers.prompt_loader import PromptLoader
from app.db.tickets import log_llm_call, save_product

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
    return _client


async def research_product(ticket_id: str, query: str) -> dict:
    """
    Call OpenAI to identify the product, alternatives, and store search query.
    Returns the structured product dict and persists it to DB.
    """
    loader = PromptLoader()
    system_prompt = loader.load_prompt("product_research") or "Identify the product. Respond JSON."

    start = time.time()
    client = _get_client()

    resp = await client.chat.completions.create(
        model=Config.OPENAI_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    latency = int((time.time() - start) * 1000)
    result = json.loads(raw)

    # Cap alternatives to MAX_ALTERNATIVES
    alts = result.get("alternatives") or []
    result["alternatives"] = alts[: Config.MAX_ALTERNATIVES]

    log_llm_call(
        ticket_id=ticket_id, step="product_research", model=Config.OPENAI_MODEL,
        prompt_template="product_research.txt",
        input_data={"query": query}, output_data=result, raw_response=raw,
        tokens_input=resp.usage.prompt_tokens if resp.usage else 0,
        tokens_output=resp.usage.completion_tokens if resp.usage else 0,
        latency_ms=latency,
    )

    save_product(ticket_id, result)
    return result
