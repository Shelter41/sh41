"""Read-only adapter for Ollama's public library pages (not a stable JSON API)."""
import re
from html.parser import HTMLParser

import httpx


class LibraryPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}
        self.current = None
        self.parts = []
        self.more = False
        self.empty = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and attrs.get("hx-get", "").startswith("/search?page="):
            self.more = True
        if tag == "a":
            self.current = attrs.get("href", "")
            self.parts = []

    def handle_data(self, data):
        if "No models found." in data:
            self.empty = True
        if self.current:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            content = " ".join(" ".join(self.parts).split())
            if len(content) > len(self.links.get(self.current, "")):
                self.links[self.current] = content
            self.current = None


def page(path, **params):
    try:
        # Normal search requests redirect pagination back to page one.
        with httpx.stream("GET", "https://ollama.com" + path, params=params,
                          headers={"HX-Request": "true"} if path == "/search" else {},
                          timeout=10, follow_redirects=True) as response:
            response.raise_for_status()
            chunks, length = [], 0
            for chunk in response.iter_bytes():
                length += len(chunk)
                if length > 5_000_000:
                    raise ValueError("Ollama library response is too large")
                chunks.append(chunk)
        parsed = LibraryPage()
        parsed.feed(b"".join(chunks).decode("utf-8"))
        return parsed
    except (httpx.HTTPError, UnicodeError) as exc:
        raise ValueError("Ollama library unavailable; retry or use a downloaded/custom model") from exc


def search(query="", number=1):
    result = page("/search", c="tools", q=query, page=number)
    names = []
    for href, content in result.links.items():
        match = re.fullmatch(r"/library/([a-zA-Z0-9][a-zA-Z0-9._-]*)", href)
        if match:
            # Cloud-only families have no parameter-size badge on the library card.
            if re.search(r"\bcloud\b", content) and not re.search(r"\b\d+(?:\.\d+)?[bm]\b", content, re.I):
                continue
            names.append(match[1])
    if not names and not result.links and not result.empty:
        raise ValueError("Ollama library format unavailable; use a downloaded/custom model")
    return names, result.more


def variants(family):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", family):
        raise ValueError("Invalid Ollama library family")
    result = page(f"/library/{family}/tags")
    rows = []
    for href, content in result.links.items():
        match = re.fullmatch(r"/library/" + re.escape(family) + r":([a-zA-Z0-9][a-zA-Z0-9._-]*)", href)
        if not match or "cloud" in match[1].lower():
            continue
        size = re.search(r"\b\d+(?:\.\d+)?\s*[KMGT]B\b", content)
        if size:
            rows.append((family + ":" + match[1], size[0]))
    return rows
