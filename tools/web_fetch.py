"""URL content fetching tool."""

import asyncio
import re
import ssl
from html import unescape
from urllib.parse import urlparse


def _build_ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SCHEMA = {
    "name": "web_fetch",
    "description": (
        "Fetch a webpage and return its text content (HTML stripped). "
        "Use this to read specific URLs found via web_search. "
        "Returns up to 8000 characters of clean text. "
        "Much faster and more reliable than bash+curl for reading web pages."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
            "extract": {
                "type": "string",
                "description": "Optional: specific text to look for in the page. If provided, returns only the surrounding context (~2000 chars) around the first match.",
            },
        },
        "required": ["url"],
    },
}

_TIMEOUT = 20  # seconds
_MAX_OUTPUT = 8000  # characters

_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
}


def _is_url_safe(url: str) -> bool:
    import ipaddress

    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False

        host = parsed.hostname or ""
        host_lower = host.lower()

        if host_lower in _BLOCKED_HOSTNAMES:
            return False

        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            import socket
            try:
                resolved = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
                if resolved:
                    addr = ipaddress.ip_address(resolved[0][4][0])
                else:
                    addr = None
            except (socket.gaierror, ValueError, OSError):
                addr = None

        if addr is not None:
            if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
                addr = addr.ipv4_mapped
            if (addr.is_private or addr.is_reserved or addr.is_loopback
                    or addr.is_link_local or addr.is_multicast
                    or addr.is_unspecified):
                return False

        return True
    except Exception:
        return False


def _html_to_text(html: str) -> str:
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S | re.I)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.S | re.I)
    html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.S | re.I)
    html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.S | re.I)
    html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.S | re.I)
    html = re.sub(r'<br\s*/?\s*>', '\n', html, flags=re.I)
    html = re.sub(r'</(p|div|tr|li|h[1-6])>', '\n', html, flags=re.I)
    html = re.sub(r'<(p|div|tr|li|h[1-6])[^>]*>', '\n', html, flags=re.I)
    html = re.sub(r'<t[dh][^>]*>', ' | ', html, flags=re.I)
    html = re.sub(r'<[^>]+>', '', html)
    text = unescape(html)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()


async def _fetch_wikipedia(lang: str, title: str, extract: str = "") -> str:
    import aiohttp
    import urllib.parse

    title = urllib.parse.unquote(title).replace(' ', '_')
    api_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/html/{urllib.parse.quote(title)}"

    try:
        timeout_cfg = aiohttp.ClientTimeout(total=_TIMEOUT)
        headers = {
            "User-Agent": "MetaTeam/1.0 (research; contact@example.com)",
            "Accept": "text/html; charset=utf-8",
        }
        connector = aiohttp.TCPConnector(ssl=_build_ssl_ctx())
        async with aiohttp.ClientSession(
            timeout=timeout_cfg, headers=headers, connector=connector, trust_env=True
        ) as session:
            async with session.get(api_url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return f"[Wikipedia API returned HTTP {resp.status} for {title}]"
                html = await resp.text()

        text = _html_to_text(html)

        if extract and extract.strip():
            pattern = re.escape(extract.strip())
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                start = max(0, match.start() - 1000)
                end = min(len(text), match.end() + 1000)
                return f"[Wikipedia: {title} — match for '{extract}']\n\n...{text[start:end]}..."
            for kw in extract.strip().split():
                idx = text.lower().find(kw.lower())
                if idx >= 0:
                    start = max(0, idx - 1000)
                    end = min(len(text), idx + 1000)
                    return f"[Wikipedia: {title} — partial match '{kw}']\n\n...{text[start:end]}..."
            return f"[Wikipedia: {title} — '{extract}' not found]\n\n{text[:_MAX_OUTPUT]}"

        if len(text) > _MAX_OUTPUT:
            return f"[Wikipedia: {title}]\n\n{text[:_MAX_OUTPUT]}\n\n[...truncated, {len(text)} chars total]"
        return f"[Wikipedia: {title}]\n\n{text}"

    except asyncio.TimeoutError:
        return f"[Wikipedia timeout for {title}]"
    except Exception as e:
        return f"[Wikipedia fetch error: {type(e).__name__}: {e}]"


async def execute(url: str, extract: str = "", **kwargs) -> str:
    if not url.strip():
        return "[error: empty URL]"

    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    if not _is_url_safe(url):
        return "[error: blocked URL — internal/private addresses are not allowed]"

    try:
        import aiohttp
    except ImportError:
        return "[error: aiohttp not installed — run: pip install aiohttp]"

    wiki_match = re.match(
        r'https?://([a-z]{2,3})\.wikipedia\.org/wiki/(.+)', url
    )
    if wiki_match:
        return await _fetch_wikipedia(wiki_match.group(1), wiki_match.group(2), extract)

    try:
        timeout_cfg = aiohttp.ClientTimeout(total=_TIMEOUT)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        }

        async with aiohttp.ClientSession(
            timeout=timeout_cfg,
            headers=headers,
            connector=aiohttp.TCPConnector(ssl=_build_ssl_ctx()),
            trust_env=True,
        ) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return f"[HTTP {resp.status} for {url}]"

                content_type = resp.headers.get("Content-Type", "")
                if "text" not in content_type and "html" not in content_type and "json" not in content_type:
                    return f"[non-text content: {content_type}]"

                body = await resp.read()
                text = body[:500_000].decode(errors="replace")

        if "<html" in text.lower() or "<body" in text.lower():
            text = _html_to_text(text)

        if extract and extract.strip():
            pattern = re.escape(extract.strip())
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                start = max(0, match.start() - 1000)
                end = min(len(text), match.end() + 1000)
                context = text[start:end]
                return f"[Found match for '{extract}' in {url}]\n\n...{context}..."
            else:
                keywords = extract.strip().split()
                for kw in keywords:
                    idx = text.lower().find(kw.lower())
                    if idx >= 0:
                        start = max(0, idx - 1000)
                        end = min(len(text), idx + 1000)
                        context = text[start:end]
                        return f"[Partial match for keyword '{kw}' in {url}]\n\n...{context}..."
                return f"['{extract}' not found in page. Showing first {_MAX_OUTPUT} chars]\n\n{text[:_MAX_OUTPUT]}"

        if len(text) > _MAX_OUTPUT:
            return f"{text[:_MAX_OUTPUT]}\n\n[...truncated, showing {_MAX_OUTPUT}/{len(text)} chars]"
        return text

    except asyncio.TimeoutError:
        return f"[timeout after {_TIMEOUT}s fetching {url}]"
    except Exception as e:
        return f"[fetch error: {type(e).__name__}: {e}]"
