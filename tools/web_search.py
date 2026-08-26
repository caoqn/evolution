"""Web search tool using DuckDuckGo."""

import asyncio
import logging
import os
import random

logger = logging.getLogger(__name__)


_SERPAPI_BASE_URL = "https://serpapi.com/search"
_SERPER_BASE_URL = "https://google.serper.dev/search"
_SERPAPI_TIMEOUT = 120
_SERPER_TIMEOUT = 30
_engine_detected = False


def _get_engine() -> tuple[str, str]:
    global _engine_detected
    serper_key = os.environ.get("SERPER_KEY_ID", "")
    serpapi_key = os.environ.get("SERPAPI_API_KEY", "")
    if serper_key:
        engine, api_key = "serper", serper_key
    elif serpapi_key:
        engine, api_key = "serpapi", serpapi_key
    else:
        engine, api_key = "duckduckgo", ""
    if not _engine_detected:
        _engine_detected = True
        if engine == "serper":
            logger.info("web_search: using Serper Google Search (SERPER_KEY_ID found)")
        elif engine == "serpapi":
            logger.info("web_search: using SerpAPI Google Search (SERPAPI_API_KEY found)")
        else:
            logger.info("web_search: using DuckDuckGo fallback (no SERPER_KEY_ID / SERPAPI_API_KEY)")
    return engine, api_key


# Schema

SCHEMA = {
    "name": "web_search",
    "description": (
        "Search the web (Google via Serper if SERPER_KEY_ID is set, "
        "else SerpAPI if SERPAPI_API_KEY is set, otherwise DuckDuckGo) "
        "and return results. "
        "Returns a list of results with title, URL, and snippet. "
        "Separate different queries with commas to search multiple topics. "
        "Use specific, targeted search queries for best results. "
        "Use English queries for better result quality. "
        "For follow-up details, use web_fetch to read specific URLs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search query. Must be in English. "
                    "Separate different queries with commas. "
                    "Example: \"current US president, capital of Canada\""
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results per query (default 5, max 10).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------


async def _search_serpapi_google(query: str, top_k: int, api_key: str) -> str:
    import aiohttp
    import ssl

    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "output": "json",
        "source": "python",
        "num": str(top_k),
    }

    try:
        if os.environ.get("SERPAPI_DISABLE_SSL_VERIFY") == "1":
            logger.warning("SSL verification disabled for SerpAPI (SERPAPI_DISABLE_SSL_VERIFY=1)")
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        else:
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        timeout = aiohttp.ClientTimeout(total=_SERPAPI_TIMEOUT)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, trust_env=True
        ) as session:
            async with session.get(_SERPAPI_BASE_URL, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return f"[SerpAPI error: HTTP {resp.status}: {text[:200]}]"

                data = await resp.json()

                if "error" in data:
                    return f"[SerpAPI error: {data['error']}]"

                organic_results = data.get("organic_results", [])
                if not organic_results:
                    return f"[no results found for: {query}]"

                output_parts = []
                for r in organic_results[:top_k]:
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")
                    link = r.get("link", "")

                    part = f"title: {title}\nsnippet: {snippet}\n"

                    rich_snippet = r.get("rich_snippet", {})
                    if isinstance(rich_snippet, dict):
                        bottom = rich_snippet.get("bottom", {})
                        if isinstance(bottom, dict):
                            extensions = bottom.get("extensions", [])
                            if extensions:
                                part += "RichSnippet: " + ", ".join(
                                    str(e) for e in extensions) + "\n"

                    part += f"link: {link}\n"
                    output_parts.append(part)

                return "\n".join(output_parts)

    except asyncio.TimeoutError:
        return f"[SerpAPI timeout after {_SERPAPI_TIMEOUT}s for: {query}]"
    except Exception as e:
        return f"[SerpAPI error: {type(e).__name__}: {e}]"


# ---------------------------------------------------------------------------

async def _search_serper_google(query: str, top_k: int, api_key: str) -> str:
    import aiohttp
    import json as _json
    import ssl

    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_ctx = ssl.create_default_context()

    payload = _json.dumps({"q": query, "num": top_k}).encode()
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    try:
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        timeout = aiohttp.ClientTimeout(total=_SERPER_TIMEOUT)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, trust_env=True
        ) as session:
            async with session.post(_SERPER_BASE_URL, data=payload, headers=headers) as resp:
                if resp.status == 401 or resp.status == 403:
                    return f"[Serper auth error: HTTP {resp.status} — check SERPER_KEY_ID]"
                if resp.status == 429:
                    return f"[Serper rate limit: HTTP 429 for: {query}]"
                if resp.status != 200:
                    text = await resp.text()
                    return f"[Serper error: HTTP {resp.status}: {text[:200]}]"

                data = await resp.json()

                output_parts = []

                if "answerBox" in data:
                    ab = data["answerBox"]
                    ans = ab.get("answer", "") or ab.get("snippet", "")
                    if ans:
                        output_parts.append(
                            f"[ANSWER BOX] {ab.get('title','')}\n"
                            f"answer: {ans}\n"
                        )

                organic = data.get("organic", [])
                if not organic and not output_parts:
                    return f"[no results found for: {query}]"

                for r in organic[:top_k]:
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")
                    link = r.get("link", "")
                    output_parts.append(
                        f"title: {title}\n"
                        f"snippet: {snippet}\n"
                        f"link: {link}\n"
                    )

                return "\n".join(output_parts)

    except asyncio.TimeoutError:
        return f"[Serper timeout after {_SERPER_TIMEOUT}s for: {query}]"
    except Exception as e:
        return f"[Serper error: {type(e).__name__}: {e}]"


# DuckDuckGo Fallback
# ---------------------------------------------------------------------------

async def _search_duckduckgo(query: str, top_k: int) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return "[error: ddgs not installed — run: pip install -e .]"

    try:
        def _search():
            proxy = (
                os.environ.get("HTTPS_PROXY")
                or os.environ.get("https_proxy")
                or os.environ.get("ALL_PROXY")
                or os.environ.get("all_proxy")
            )
            return DDGS(proxy=proxy).text(query, max_results=top_k, region="wt-wt")

        results = await asyncio.wait_for(
            asyncio.to_thread(_search), timeout=30.0
        )

        if not results:
            return f"[no results found for: {query}]"

        output_parts = []
        for r in results:
            title = r.get("title", "No title")
            href = r.get("href", "")
            body = r.get("body", "")
            output_parts.append(
                f"title: {title}\n"
                f"snippet: {body}\n"
                f"link: {href}\n"
            )

        return "\n".join(output_parts)

    except asyncio.TimeoutError:
        return f"[search timeout for: {query}]"
    except Exception as e:
        return f"[search error: {e}]"



_MAX_RETRIES = 3
_BASE_DELAY = 1.0


async def execute(query: str, max_results: int = 5, **kwargs) -> str:
    if not query.strip():
        return "[error: empty query]"

    max_results = min(max(1, max_results), 10)

    queries = [q.strip() for q in query.split(",") if q.strip()]
    if not queries:
        return "[error: empty query]"

    queries = queries[:5]

    engine, api_key = _get_engine()

    async def _do_search(q: str, k: int) -> str:
        if engine == "serper":
            return await _search_serper_google(q, k, api_key)
        if engine == "serpapi":
            return await _search_serpapi_google(q, k, api_key)
        return await _search_duckduckgo(q, k)

    combined_results = []

    for i, q in enumerate(queries):
        result = None
        last_error = None

        for attempt in range(_MAX_RETRIES):
            try:
                result = await _do_search(q, max_results)
                if not result.startswith("[") or "error" not in result.lower():
                    break
                last_error = result
            except Exception as e:
                last_error = f"[search error: {e}]"

            if attempt < _MAX_RETRIES - 1:
                delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                await asyncio.sleep(delay)

        if result and not (result.startswith("[") and "error" in result.lower()):
            if len(queries) > 1:
                combined_results.append(
                    f"Search result of query {i + 1} ({q}):\n{result}")
            else:
                combined_results.append(result)
        else:
            combined_results.append(
                f"Search result of query {i + 1}: Query Search Engine Error, "
                f"Please Try Again\n{last_error or ''}")

    return "\n".join(combined_results)
