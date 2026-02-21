"""Web Deals Finder — uses Gemini with Google Search grounding aggressively.

Fires MULTIPLE parallel grounded searches, each targeting a different angle
(price comparison, deals/coupons, quick commerce, niche platforms), then merges
and deduplicates everything into a single structured result.
"""
import asyncio
import json
import time
import logging
from typing import Any

from google import genai
from google.genai import types

from app.helpers.config import Config
from app.db.tickets import log_llm_call, save_web_deals
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


SEARCH_ANGLES = [
    {
        "id": "price_compare",
        "prompt": (
            "You MUST use Google Search for this. Search the web RIGHT NOW.\n\n"
            "Find the current price for {product} on every major Indian e-commerce platform. "
            "Search specifically on: Amazon.in, Flipkart, JioMart, Croma, Reliance Digital, Tata CLiQ, Vijay Sales.\n\n"
            "For EACH platform, tell me:\n"
            "- Exact product listing title\n"
            "- Current selling price in INR\n"
            "- Original/MRP price if discounted\n"
            "- Direct product page URL\n"
            "- Whether it's in stock\n\n"
            "Search query suggestions: \"{product} price India\", \"{product} buy online India\", "
            "\"{product} Amazon.in price\", \"{product} Flipkart price\"\n\n"
            "Be thorough. Check EVERY platform. Report what you actually find from search."
        ),
    },
    {
        "id": "deals_offers",
        "prompt": (
            "You MUST use Google Search for this. Search the web RIGHT NOW.\n\n"
            "Find ALL active deals, offers, discounts, and coupons for {product} in India.\n\n"
            "Search for:\n"
            "- Bank card offers (HDFC, ICICI, SBI, Axis) on this product\n"
            "- Active coupon codes on any platform\n"
            "- Cashback offers (Paytm, PhonePe, Amazon Pay)\n"
            "- Exchange/trade-in offers\n"
            "- Bundle deals or combo offers\n"
            "- EMI/no-cost EMI options\n"
            "- Any ongoing sale events (Big Billion Days, Great Indian Festival, Republic Day sale, etc.)\n\n"
            "Search query suggestions: \"{product} offer today\", \"{product} coupon code 2026\", "
            "\"{product} bank offer discount\", \"{product} best deal India\", \"{product} price drop\"\n\n"
            "Report ONLY real, currently active offers you find via search. Include source URLs."
        ),
    },
    {
        "id": "quick_commerce",
        "prompt": (
            "You MUST use Google Search for this. Search the web RIGHT NOW.\n\n"
            "Check if {product} is available on quick commerce and instant delivery platforms in India.\n\n"
            "Specifically search on:\n"
            "- Zepto\n"
            "- Blinkit (Zomato)\n"
            "- Swiggy Instamart\n"
            "- BigBasket (BB Now for instant delivery)\n"
            "- Dunzo\n"
            "- Amazon Fresh / Amazon Now\n"
            "- JioMart Express\n\n"
            "For each platform where available, report:\n"
            "- Price\n"
            "- Delivery time estimate\n"
            "- Product URL if available\n"
            "- Any delivery offers\n\n"
            "Search query suggestions: \"{product} Zepto\", \"{product} Blinkit\", "
            "\"{product} Swiggy Instamart\", \"{product} quick delivery\", \"{product} 10 minute delivery\"\n\n"
            "This is VERY important — users love fast delivery. Even if the product seems unusual "
            "for quick commerce (like electronics or medicines), still check. "
            "Quick commerce platforms are expanding into everything."
        ),
    },
    {
        "id": "niche_surprise",
        "prompt": (
            "You MUST use Google Search for this. Search the web RIGHT NOW.\n\n"
            "Find {product} on LESSER-KNOWN or UNEXPECTED platforms and sources in India. "
            "The user already knows about Amazon and Flipkart — find them something they'd NEVER think to check.\n\n"
            "Search on:\n"
            "- Meesho, Snapdeal, ShopClues (budget platforms)\n"
            "- IndiaMART, TradeIndia (wholesale/bulk)\n"
            "- Official brand website / brand store\n"
            "- Niche category sites (e.g., Nykaa for beauty, Lenskart for eyewear, "
            "PharmEasy/1mg/Netmeds for medicines, Decathlon for sports)\n"
            "- Regional e-commerce (ShopBy, DealShare, Mall91)\n"
            "- Refurbished/open-box (Cashify, Budli, OverCart, Amazon Renewed)\n"
            "- International with India shipping (AliExpress, eBay India)\n"
            "- Social commerce (Instagram shops, WhatsApp catalogs mentioned in search)\n"
            "- Price comparison sites (PriceDekho, MySmartPrice, Smartprix, PriceHunt) — "
            "these often surface deals the user wouldn't find directly\n\n"
            "Search query suggestions: \"{product} cheapest price India\", \"{product} price comparison\", "
            "\"{product} Meesho\", \"{product} refurbished\", \"{product} wholesale price\", "
            "\"{product} PriceDekho\"\n\n"
            "The WHOLE POINT is to find something surprising. A deal on a platform nobody would've checked."
        ),
    },
]


async def search_web_deals(
    ticket_id: str,
    query: str,
    product: dict[str, Any] | None = None,
    location: str = "",
) -> dict[str, Any]:
    """
    Fire multiple parallel Google-grounded searches, each targeting a different
    angle (prices, deals, quick commerce, niche). Merge and deduplicate results.
    """
    product_name = query
    if product:
        product_name = product.get("product_name") or query

    product_context = ""
    if product:
        product_context = (
            f"\nProduct: {product.get('product_name', 'unknown')}"
            f"\nCategory: {product.get('product_category', 'unknown')}"
            f"\nSpecs: {json.dumps(product.get('specs') or {})}"
            f"\nEstimated avg price: ₹{product.get('avg_price_online', 'unknown')}"
        )
    location_context = f"\nUser location: {location}" if location else ""

    start = time.time()

    tasks = [
        _grounded_search(
            ticket_id=ticket_id,
            angle=angle,
            product_name=product_name,
            product_context=product_context,
            location_context=location_context,
        )
        for angle in SEARCH_ANGLES
    ]

    raw_results = await asyncio.gather(*tasks)

    all_raw_texts = []
    all_grounding_sources = []
    all_search_queries = []

    for angle, (text, grounding) in zip(SEARCH_ANGLES, raw_results):
        if text:
            all_raw_texts.append(f"=== {angle['id'].upper()} RESULTS ===\n{text}")
        if grounding:
            for src in grounding.get("sources", []):
                if src not in all_grounding_sources:
                    all_grounding_sources.append(src)
            all_search_queries.extend(grounding.get("search_queries", []))

    merged_raw = "\n\n".join(all_raw_texts)
    latency_search = int((time.time() - start) * 1000)
    logger.info(
        "Ticket %s: %d grounded searches completed in %dms, %d sources found",
        ticket_id, len(SEARCH_ANGLES), latency_search, len(all_grounding_sources),
    )

    result = await _synthesize_results(
        ticket_id=ticket_id,
        product_name=product_name,
        merged_raw=merged_raw,
        grounding_sources=all_grounding_sources,
    )

    if all_grounding_sources or all_search_queries:
        result["_grounding_metadata"] = {
            "sources": all_grounding_sources,
            "search_queries": list(set(all_search_queries)),
            "search_angles": len(SEARCH_ANGLES),
        }

    total_latency = int((time.time() - start) * 1000)

    usage_estimate = len(merged_raw) // 4
    log_llm_call(
        ticket_id=ticket_id,
        step="web_deals_search",
        model=Config.GEMINI_MODEL,
        prompt_template="web_deals.txt",
        input_data={
            "query": query,
            "product_name": product_name,
            "search_angles": [a["id"] for a in SEARCH_ANGLES],
            "sources_found": len(all_grounding_sources),
        },
        output_data=result,
        raw_response=merged_raw[:5000],
        tokens_input=usage_estimate,
        tokens_output=usage_estimate // 2,
        latency_ms=total_latency,
    )

    save_web_deals(ticket_id, result)

    deal_count = len(result.get("deals") or [])
    best = result.get("best_deal") or {}
    logger.info(
        "Ticket %s web deals: %d deals from %d sources in %dms (best: %s ₹%s)",
        ticket_id, deal_count, len(all_grounding_sources), total_latency,
        best.get("platform", "N/A"), best.get("price", "N/A"),
    )

    return result


async def _grounded_search(
    ticket_id: str,
    angle: dict,
    product_name: str,
    product_context: str,
    location_context: str,
) -> tuple[str, dict[str, Any] | None]:
    """Single grounded Gemini call for one search angle. Returns (raw_text, grounding_metadata)."""
    client = _get_client()
    prompt = angle["prompt"].replace("{product}", product_name)
    full_prompt = (
        f"{prompt}\n{product_context}{location_context}\n\n"
        f"Report everything you find. Be specific — exact prices, exact URLs, exact product names. "
        f"Do NOT make anything up. Only report what Google Search actually shows you."
    )

    try:
        response = await client.aio.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=1.0,
            ),
        )
        text = response.text or ""
        grounding = _extract_grounding_metadata(response)
        logger.debug(
            "Ticket %s [%s]: got %d chars, %d sources",
            ticket_id, angle["id"], len(text),
            len((grounding or {}).get("sources", [])),
        )
        return text, grounding

    except Exception:
        logger.warning("Ticket %s [%s] grounded search failed", ticket_id, angle["id"], exc_info=True)
        return "", None


async def _synthesize_results(
    ticket_id: str,
    product_name: str,
    merged_raw: str,
    grounding_sources: list[dict],
) -> dict[str, Any]:
    """Take all raw grounded search results and synthesize into structured JSON."""
    if not merged_raw.strip():
        return {"deals": [], "search_summary": "Web search returned no results."}

    loader = PromptLoader()
    system_prompt = loader.load_prompt("web_deals") or "Structure the deals. Respond JSON."

    sources_context = ""
    if grounding_sources:
        source_lines = [
            f"  - {s.get('title', 'Unknown')}: {s.get('uri', 'N/A')}"
            for s in grounding_sources[:30]
        ]
        sources_context = (
            f"\n\nVERIFIED SOURCES FROM GOOGLE SEARCH ({len(grounding_sources)} total):\n"
            + "\n".join(source_lines)
        )

    synthesis_prompt = (
        f"{system_prompt}\n\n"
        f"PRODUCT: {product_name}\n\n"
        f"Below are the RAW results from MULTIPLE parallel Google searches we ran. "
        f"Each section covers a different search angle (price comparison, deals/offers, "
        f"quick commerce, niche platforms).\n\n"
        f"YOUR JOB: Merge, deduplicate, and structure ALL of this into the JSON format "
        f"specified above. Cross-reference prices. Identify the actual best deal. "
        f"Don't lose any deal — if something was found, include it.\n\n"
        f"--- RAW SEARCH RESULTS ---\n{merged_raw}\n"
        f"{sources_context}\n\n"
        f"Now produce the final structured JSON. Include URLs from the verified sources above "
        f"where they match the deals found."
    )

    client = _get_client()
    try:
        response = await client.aio.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=synthesis_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        raw = response.text or "{}"
        return _parse_json(raw)
    except Exception:
        logger.warning("Synthesis call failed, attempting raw parse", exc_info=True)
        return _parse_json(merged_raw)


def _parse_json(raw: str) -> dict[str, Any]:
    """Parse JSON from Gemini response, handling markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
        if "deals" not in result:
            result["deals"] = []
        return result
    except json.JSONDecodeError:
        logger.warning("Could not parse web deals response as JSON")
        return {
            "deals": [],
            "search_summary": text[:500],
            "parse_error": True,
        }


def _extract_grounding_metadata(response) -> dict[str, Any] | None:
    """Extract grounding metadata (search queries, source URLs) from the response."""
    try:
        candidate = response.candidates[0] if response.candidates else None
        if not candidate:
            return None

        metadata = getattr(candidate, "grounding_metadata", None)
        if not metadata:
            return None

        result = {}

        queries = getattr(metadata, "web_search_queries", None)
        if queries:
            result["search_queries"] = list(queries)

        chunks = getattr(metadata, "grounding_chunks", None)
        if chunks:
            result["sources"] = []
            for chunk in chunks:
                web = getattr(chunk, "web", None)
                if web:
                    result["sources"].append({
                        "uri": getattr(web, "uri", None),
                        "title": getattr(web, "title", None),
                    })

        supports = getattr(metadata, "grounding_supports", None)
        if supports:
            result["citation_count"] = len(supports)

        return result if result else None
    except Exception:
        logger.debug("Could not extract grounding metadata", exc_info=True)
        return None
