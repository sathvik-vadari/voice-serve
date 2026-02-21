"""Gemini AI client — intelligent query analysis and store re-ranking."""
import json
import time
import logging
from typing import Any

from google import genai
from google.genai import types

from app.helpers.config import Config
from app.db.tickets import log_llm_call
from app.helpers.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not Config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=Config.GEMINI_API_KEY)
    return _client


async def analyze_query(ticket_id: str, query: str, location: str) -> dict[str, Any]:
    """
    Use Gemini to intelligently analyze a user query.
    Detects specific store/restaurant names vs generic product requests
    and generates smart Google Maps search strategies.
    """
    loader = PromptLoader()
    system_prompt = loader.load_prompt("query_analyzer") or "Analyze the query. Respond JSON."

    user_message = f"Query: {query}\nLocation: {location}"

    start = time.time()
    client = _get_client()

    response = await client.aio.models.generate_content(
        model=Config.GEMINI_MODEL,
        contents=f"{system_prompt}\n\n{user_message}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    raw = response.text or "{}"
    latency = int((time.time() - start) * 1000)
    result = json.loads(raw)

    usage = getattr(response, "usage_metadata", None)
    log_llm_call(
        ticket_id=ticket_id, step="query_analyzer", model=Config.GEMINI_MODEL,
        prompt_template="query_analyzer.txt",
        input_data={"query": query, "location": location},
        output_data=result, raw_response=raw,
        tokens_input=getattr(usage, "prompt_token_count", 0) if usage else 0,
        tokens_output=getattr(usage, "candidates_token_count", 0) if usage else 0,
        latency_ms=latency,
    )

    if "search_queries" not in result or not result["search_queries"]:
        result["search_queries"] = [f"{query} near {location}"]

    return result


async def rerank_stores(
    ticket_id: str, query: str, stores: list[dict], query_analysis: dict,
) -> list[dict]:
    """
    Use Gemini to re-rank store results based on relevance to the query.
    Prioritizes exact store name matches, then category relevance.
    """
    if not stores or len(stores) <= 1:
        return stores

    client = _get_client()

    stores_summary = json.dumps([
        {
            "idx": i,
            "name": s.get("name"),
            "address": s.get("address"),
            "rating": s.get("rating"),
            "total_ratings": s.get("total_ratings"),
        }
        for i, s in enumerate(stores)
    ], indent=2)

    specific_store = query_analysis.get("specific_store_name") or ""
    product_cat = query_analysis.get("product_category") or ""

    prompt = f"""User query: "{query}"
Specific store requested: "{specific_store}"
Product category: "{product_cat}"

Stores found:
{stores_summary}

Re-rank by relevance. Highest priority: exact or close name match to the requested store. Then: category relevance, rating.

Respond JSON only:
{{"ranked_indices": [0, 2, 1], "reasoning": "brief explanation"}}"""

    try:
        start = time.time()
        response = await client.aio.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        raw = response.text or "{}"
        result = json.loads(raw)
        latency = int((time.time() - start) * 1000)

        usage = getattr(response, "usage_metadata", None)
        log_llm_call(
            ticket_id=ticket_id, step="store_reranking", model=Config.GEMINI_MODEL,
            prompt_template="inline",
            input_data={"query": query, "store_count": len(stores)},
            output_data=result, raw_response=raw,
            tokens_input=getattr(usage, "prompt_token_count", 0) if usage else 0,
            tokens_output=getattr(usage, "candidates_token_count", 0) if usage else 0,
            latency_ms=latency,
        )

        indices = result.get("ranked_indices", [])
        reranked = []
        seen = set()
        for i in indices:
            if isinstance(i, int) and 0 <= i < len(stores) and i not in seen:
                reranked.append(stores[i])
                seen.add(i)
        for i, s in enumerate(stores):
            if i not in seen:
                reranked.append(s)
        return reranked

    except Exception:
        logger.exception("Gemini store re-ranking failed, using original order")
        return stores
