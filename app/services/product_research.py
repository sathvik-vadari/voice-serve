"""Product Research LLM – identifies product details, specs, alternatives, and store search term."""
import json
import time
import logging
from typing import Any

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


async def research_product(
    ticket_id: str, query: str, query_analysis: dict[str, Any] | None = None,
) -> dict:
    """
    Call OpenAI to identify the product, alternatives, and store search query.
    Accepts optional query_analysis from Gemini for better context.
    Returns the structured product dict and persists it to DB.
    """
    loader = PromptLoader()
    system_prompt = loader.load_prompt("product_research") or "Identify the product. Respond JSON."

    user_content = query
    if query_analysis:
        context = json.dumps(query_analysis, indent=2)
        user_content = (
            f"User query: {query}\n\n"
            f"Query analysis (from our intelligence system):\n{context}"
        )

    start = time.time()
    client = _get_client()

    resp = await client.chat.completions.create(
        model=Config.OPENAI_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    latency = int((time.time() - start) * 1000)
    result = json.loads(raw)

    alts = result.get("alternatives") or []
    result["alternatives"] = alts[: Config.MAX_ALTERNATIVES]

    if query_analysis:
        if query_analysis.get("is_specific_store") and not result.get("is_specific_store"):
            result["is_specific_store"] = True
            result["specific_store_name"] = query_analysis.get("specific_store_name")
        if query_analysis.get("search_queries"):
            result["_search_queries"] = query_analysis["search_queries"]

    log_llm_call(
        ticket_id=ticket_id, step="product_research", model=Config.OPENAI_MODEL,
        prompt_template="product_research.txt",
        input_data={"query": query, "has_analysis": bool(query_analysis)},
        output_data=result, raw_response=raw,
        tokens_input=resp.usage.prompt_tokens if resp.usage else 0,
        tokens_output=resp.usage.completion_tokens if resp.usage else 0,
        latency_ms=latency,
    )

    save_product(ticket_id, result)
    return result
