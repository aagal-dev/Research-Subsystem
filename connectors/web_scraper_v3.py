from .base_connector import BaseConnector

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse
import hashlib
import html
import math
import re
import unicodedata
import socket
import ipaddress

import requests
import trafilatura


class WebScraperConnector(BaseConnector):
    """Search top URLs with DuckDuckGo, then crawl/extract them with Trafilatura."""

    DDG_HTML = "https://html.duckduckgo.com/html/"
    DDG_LITE = "https://lite.duckduckgo.com/lite/"

    SEARCH_TIMEOUT = 15
    MAX_URLS = 10
    WORKERS = 5
    MAX_TEXT = 40_000

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; Mobile) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0 Mobile Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://html.duckduckgo.com/",
    }

    BAD_TEXT = (
        "access denied",
        "verify you are human",
        "captcha",
        "checking your browser",
        "just a moment",
        "request blocked",
    )

    @property
    def name(self):
        return "web_scraper"

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_url(url):
        try:
            p = urlparse((url or "").strip())
            if p.scheme.lower() not in {"http", "https"} or not p.hostname:
                return ""

            host = p.hostname.lower().rstrip(".")
            port = p.port
            if port and not (
                (p.scheme.lower() == "http" and port == 80)
                or (p.scheme.lower() == "https" and port == 443)
            ):
                host += f":{port}"

            query = [
                (k, v)
                for k, v in parse_qsl(p.query, keep_blank_values=True)
                if not k.lower().startswith(("utm_",))
                and k.lower() not in {"gclid", "fbclid", "mc_cid", "mc_eid"}
            ]

            return urlunparse(
                (
                    p.scheme.lower(),
                    host,
                    p.path or "/",
                    "",
                    urlencode(query, doseq=True),
                    "",
                )
            )
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _public_url(url):
        try:
            p = urlparse(url)
            host = (p.hostname or "").lower()
            if not host or p.username or p.password:
                return False
            if host in {"localhost", "localhost.localdomain"}:
                return False

            for row in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
                ip = ipaddress.ip_address(row[4][0])
                if not ip.is_global:
                    return False
            return True
        except (OSError, ValueError):
            return False

    @classmethod
    def _safe_url(cls, url):
        url = cls._canonical_url(url)
        return url if url and cls._public_url(url) else ""

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @classmethod
    def _unwrap_search_url(cls, href):
        if not href:
            return ""

        href = html.unescape(href.strip())
        p = urlparse(href)
        params = dict(parse_qsl(p.query, keep_blank_values=True))

        if params.get("uddg"):
            href = unquote(params["uddg"])

        return cls._safe_url(href)

    @classmethod
    def _parse_search(cls, body):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(body, "html.parser")
        candidates = []

        # Preferred DDG layouts.
        selectors = (
            "#links div.web-result h2 a",
            "#links .result__a",
            "a.result__a",
            'a[data-testid="result-title-a"]',
            "a.result-link",
        )

        for selector in selectors:
            for tag in soup.select(selector):
                url = cls._unwrap_search_url(tag.get("href"))
                title = tag.get_text(" ", strip=True)
                if url:
                    candidates.append({"url": url, "title": title})
            if candidates:
                break

        # Broad fallback: useful when DDG changes classes/markup.
        if not candidates:
            root = soup.select_one("#links") or soup.body or soup
            for tag in root.find_all("a", href=True):
                url = cls._unwrap_search_url(tag.get("href"))
                title = tag.get_text(" ", strip=True)
                if url and title:
                    candidates.append({"url": url, "title": title})

        unique = {}
        for item in candidates:
            unique.setdefault(item["url"], item)

        return list(unique.values())[: cls.MAX_URLS]

    @classmethod
    def _search_once(cls, endpoint, query):
        try:
            response = requests.post(
                endpoint,
                data={"q": query, "b": "", "kl": "us-en"},
                headers=cls.HEADERS,
                timeout=cls.SEARCH_TIMEOUT,
            )

            if not 200 <= response.status_code < 300:
                return [], f"{endpoint}: HTTP {response.status_code}"

            body = response.content
            lower = body.decode("utf-8", errors="replace").lower()[:50_000]

            if any(marker in lower for marker in cls.BAD_TEXT):
                return [], f"{endpoint}: challenge/block page"

            urls = cls._parse_search(body)
            if urls:
                return urls, None

            return [], f"{endpoint}: no usable URLs"

        except requests.RequestException as exc:
            return [], f"{endpoint}: {exc}"

    @classmethod
    def web_search(cls, query):
        errors = []

        for endpoint in (cls.DDG_HTML, cls.DDG_LITE):
            urls, error = cls._search_once(endpoint, query)
            if urls:
                return {
                    "ok": True,
                    "urls": urls,
                    "backend": endpoint,
                    "errors": errors,
                }
            errors.append(error)

        return {
            "ok": False,
            "urls": [],
            "backend": None,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Crawl / extraction
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Content cleaning / corruption filtering
    # ------------------------------------------------------------------

    # These limits are intentionally conservative: the cleaner removes
    # obviously machine-generated/corrupt payloads while leaving normal
    # prose, code snippets, hashes, URLs, equations, and tables alone.
    MAX_SUSPICIOUS_BLOCK_RATIO = 0.45
    MAX_REPLACEMENT_RATIO = 0.015
    MIN_BLOB_LENGTH = 180
    MIN_ENTROPY_LENGTH = 160

    @staticmethod
    def _entropy(value):
        if not value:
            return 0.0
        counts = {}
        for char in value:
            counts[char] = counts.get(char, 0) + 1
        length = len(value)
        return -sum(
            (count / length) * math.log2(count / length)
            for count in counts.values()
            if count
        )

    @classmethod
    def _suspicion_score(cls, block):
        """Score a text block for binary/encrypted/encoded corruption.

        This deliberately scores blocks rather than the whole document so a
        page containing one bad payload can still return its useful prose.
        """
        if not block:
            return 0.0

        length = len(block)
        stripped = re.sub(r"\s+", "", block)

        controls = sum(
            1
            for char in block
            if unicodedata.category(char).startswith("C")
            and char not in "\n\t\r"
        )
        replacements = block.count("\ufffd")
        non_printable = sum(
            1
            for char in block
            if not char.isprintable() and char not in "\n\t\r"
        )

        score = 0.0
        score += min(0.55, (controls / max(length, 1)) * 5.0)
        score += min(0.35, (non_printable / max(length, 1)) * 4.0)
        score += min(0.30, (replacements / max(length, 1)) * 12.0)

        # Long, whitespace-free base64/hex payloads are common symptoms of
        # encrypted/binary responses accidentally exposed as extracted text.
        if len(stripped) >= cls.MIN_BLOB_LENGTH:
            base64ish = bool(
                re.fullmatch(r"[A-Za-z0-9+/=_-]+", stripped)
                and len(stripped) % 4 in {0, 2, 3}
            )
            hexish = bool(
                re.fullmatch(r"[0-9A-Fa-f]+", stripped)
                and len(stripped) % 2 == 0
            )

            if base64ish:
                score += 0.90
            if hexish:
                score += 0.90

            # High entropy + very low natural-language structure is another
            # strong indicator, but entropy alone is never enough to delete.
            if len(stripped) >= cls.MIN_ENTROPY_LENGTH:
                entropy = cls._entropy(stripped)
                word_count = len(re.findall(r"\b[\w'-]{2,}\b", block))
                whitespace_ratio = sum(c.isspace() for c in block) / length
                if entropy >= 4.5 and word_count <= 8 and whitespace_ratio < 0.08:
                    score += 0.55

        # Typical mojibake markers from a wrong character decoding.
        mojibake = len(re.findall(r"[ÃÂâ€š€™œž]", block))
        if mojibake >= 4:
            score += min(0.35, mojibake / max(length, 1) * 10.0)

        return min(score, 1.0)

    @classmethod
    def _clean_text(cls, text):
        """Normalize extracted text and remove strongly corrupted blocks."""
        text = html.unescape(text or "")
        text = unicodedata.normalize("NFKC", text)
        text = text.replace("\xa0", " ").replace("\u200b", "")

        # Strip NUL/control characters while preserving normal whitespace.
        text = "".join(
            char
            for char in text
            if char in "\n\r\t"
            or not unicodedata.category(char).startswith("C")
        )

        # Normalize line endings before block-level filtering.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        raw_blocks = re.split(r"\n\s*\n+", text)

        kept_blocks = []
        suspicious_chars = 0
        total_chars = max(len(text), 1)

        for block in raw_blocks:
            block = block.strip()
            if not block:
                continue

            suspicious = cls._suspicion_score(block)

            # Remove only strongly suspicious blocks. This is deliberately
            # stricter for long payloads and more permissive for short text.
            if suspicious >= 0.78 and len(block) >= 120:
                continue

            # For a block that contains mostly ordinary prose plus a corrupt
            # line, filter suspicious individual lines instead of discarding
            # the entire block.
            if suspicious >= 0.48:
                lines = []
                for line in block.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    line_score = cls._suspicion_score(line)
                    if line_score < 0.78 or len(line) < 120:
                        lines.append(line)
                    else:
                        suspicious_chars += len(line)
                block = "\n".join(lines).strip()
                if not block:
                    continue

            suspicious_chars += sum(
                1
                for char in block
                if char == "\ufffd"
                or (
                    not char.isprintable()
                    and char not in "\n\t\r"
                )
            )
            kept_blocks.append(block)

        text = "\n\n".join(kept_blocks)

        # Final whitespace cleanup. Do not flatten newlines: they carry useful
        # structure for articles, lists, code, and tables.
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        if suspicious_chars / total_chars > cls.MAX_REPLACEMENT_RATIO:
            # The caller/quality gate will reject severely corrupted output.
            pass

        return text.strip()

    @classmethod
    def _quality(cls, text):
        original = text or ""
        text = cls._clean_text(original)
        if not text:
            return {"usable": False, "score": 0.0, "reason": "empty extraction"}

        words = re.findall(r"\b[\w'-]+\b", text.lower())
        unique_ratio = len(set(words)) / max(len(words), 1)

        replacement_ratio = text.count("\ufffd") / max(len(text), 1)
        suspicious_blocks = [
            block for block in re.split(r"\n\s*\n+", text)
            if cls._suspicion_score(block) >= 0.78
        ]
        suspicious_chars = sum(len(block) for block in suspicious_blocks)
        suspicious_ratio = suspicious_chars / max(len(text), 1)

        if replacement_ratio > cls.MAX_REPLACEMENT_RATIO:
            return {
                "usable": False,
                "score": 0.0,
                "reason": "text encoding appears corrupted",
            }

        if suspicious_ratio > cls.MAX_SUSPICIOUS_BLOCK_RATIO:
            return {
                "usable": False,
                "score": 0.0,
                "reason": "extraction appears to contain binary or encoded payload",
            }

        if len(text) < 80:
            return {
                "usable": False,
                "score": 0.0,
                "reason": "extraction too short",
            }

        score = min(
            1.0,
            0.55
            + min(len(text) / 12_000, 1.0) * 0.30
            + unique_ratio * 0.15,
        )

        return {
            "usable": True,
            "score": round(score, 3),
            "reason": None,
        }

    @classmethod
    def _source(cls, url):
        host = (urlparse(url).hostname or "").lower()
        if host.endswith((".gov", ".gov.in", ".nic.in")):
            return {
                "domain": host,
                "type": "government",
                "tier": "primary_official",
                "score": 0.90,
                "heuristic": True,
            }
        if host.endswith((".edu", ".ac.uk", ".ac.in")):
            return {
                "domain": host,
                "type": "academic",
                "tier": "academic",
                "score": 0.84,
                "heuristic": True,
            }
        return {
            "domain": host,
            "type": "web",
            "tier": "unclassified",
            "score": 0.55,
            "heuristic": True,
        }

    @classmethod
    def _metadata(cls, html_text, url):
        try:
            metadata = trafilatura.extract_metadata(html_text, default_url=url)
            if metadata:
                return {
                    "kind": "research_paper" if getattr(metadata, "title", None) and (
                        "arxiv.org" in url
                        or getattr(metadata, "sitename", "") == "arXiv"
                    ) else "web_page",
                    "year": getattr(metadata, "date", None),
                    "authors": (
                        [metadata.author]
                        if getattr(metadata, "author", None)
                        else []
                    ),
                    "abstract": None,
                    "title": getattr(metadata, "title", None),
                }
        except Exception:
            pass

        return {
            "kind": "web_page",
            "year": None,
            "authors": [],
            "abstract": None,
            "title": None,
        }

    @classmethod
    def web_crawl(cls, item):
        url = item["url"]
        title = item.get("title", "")
        final_url = url

        try:
            html_text = trafilatura.fetch_url(url)
            if not html_text:
                return cls._failed_page(
                    url, final_url, title, "fetch_url returned no HTML"
                )

            text = trafilatura.extract(
                html_text,
                url=url,
                output_format="markdown",
                favor_recall=True,
                include_comments=False,
                include_tables=True,
                include_images=True,
                include_links=True,
                deduplicate=True,
            )

            text = cls._clean_text(text)

            if not text:
                return cls._failed_page(
                    url, final_url, title, "extraction returned no text"
                )

            quality = cls._quality(text)
            metadata = cls._metadata(html_text, url)
            title = (
                metadata.get("title")
                or title
                or ""
            ).strip()

            # Keep the connector bounded without throwing away everything.
            text = text[: cls.MAX_TEXT]

            document_id = hashlib.sha256(
                cls._canonical_url(final_url).encode()
            ).hexdigest()[:16]

            content_id = hashlib.sha256(
                re.sub(r"\s+", " ", text.lower()).encode()
            ).hexdigest()[:16]

            return {
                "status": "success",
                "url": url,
                "final_url": final_url,
                "title": title[:300],
                "text": text,
                "content_type": "text/html",
                "source": cls._source(final_url),
                "quality": quality,
                "evidence": {
                    "kind": metadata.get("kind", "web_page"),
                    "year": metadata.get("year"),
                    "authors": metadata.get("authors", [])[:30],
                    "abstract": metadata.get("abstract"),
                },
                "document_id": document_id,
                "content_id": content_id,
                "extraction": "trafilatura",
                "error": None,
            }

        except Exception as exc:
            return cls._failed_page(
                url,
                final_url,
                title,
                f"{type(exc).__name__}: {exc}",
            )

    @classmethod
    def _failed_page(cls, url, final_url, title, error):
        return {
            "status": "failed",
            "url": url,
            "final_url": final_url or url,
            "title": (title or "")[:300],
            "text": "",
            "content_type": "",
            "source": cls._source(final_url or url),
            "quality": {
                "usable": False,
                "score": 0.0,
                "reason": error,
            },
            "evidence": {
                "kind": "unknown",
                "year": None,
                "authors": [],
                "abstract": None,
            },
            "document_id": None,
            "content_id": None,
            "extraction": "trafilatura",
            "error": error,
        }

    # ------------------------------------------------------------------
    # Public connector API
    # ------------------------------------------------------------------

    def execute(self, search_query: str):
        query = str(search_query or "").strip()

        empty_summary = {
            "found": 0,
            "successful": 0,
            "failed": 0,
            "duplicates": 0,
            "usable": 0,
        }

        if not query:
            return {
                "ok": False,
                "status": "failed",
                "query": query,
                "connector": self.name,
                "results": [],
                "web_scraped_text": "",
                "pages": [],
                "summary": empty_summary,
                "errors": ["search query is empty"],
            }

        search = self.web_search(query)

        if not search["ok"]:
            return {
                "ok": False,
                "status": "failed",
                "query": query,
                "connector": self.name,
                "results": [],
                "web_scraped_text": "",
                "pages": [],
                "summary": empty_summary,
                "errors": [
                    "search failed: " + " | ".join(search["errors"])
                ],
                "search": {
                    "backend": None,
                    "urls": 0,
                },
            }

        pages = [None] * len(search["urls"])

        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = {
                pool.submit(self.web_crawl, item): i
                for i, item in enumerate(search["urls"])
            }

            for future in as_completed(futures):
                index = futures[future]
                try:
                    pages[index] = future.result()
                except Exception as exc:
                    item = search["urls"][index]
                    pages[index] = self._failed_page(
                        item["url"],
                        item["url"],
                        item.get("title", ""),
                        f"{type(exc).__name__}: {exc}",
                    )

        pages = [page for page in pages if page is not None]

        seen_urls = set()
        seen_content = set()
        results = []
        duplicates = 0

        for page in pages:
            canonical = self._canonical_url(page["url"])

            if canonical in seen_urls:
                page["status"] = "duplicate"
                page["error"] = "duplicate URL"
                duplicates += 1
                continue

            seen_urls.add(canonical)

            if page["status"] == "success":
                content_key = page.get("content_id")
                if content_key and content_key in seen_content:
                    page["status"] = "duplicate"
                    page["error"] = "duplicate content"
                    duplicates += 1
                    continue
                if content_key:
                    seen_content.add(content_key)

                results.append(page)

        successful = results
        failed = [p for p in pages if p["status"] == "failed"]

        combined = "\n\n".join(
            f"{p['title']}\n{p['text']}" if p["title"] else p["text"]
            for p in successful
        )
        combined = combined[: self.MAX_TEXT]

        if successful and not failed and duplicates == 0:
            status = "success"
        elif successful:
            status = "partial"
        else:
            status = "failed"

        errors = [
            f"{p['url']} -> {p['error']}"
            for p in pages
            if p.get("error")
        ]

        return {
            "ok": bool(successful),
            "status": status,
            "query": query,
            "connector": self.name,
            "results": successful,
            "web_scraped_text": combined,
            "pages": pages,
            "summary": {
                "found": len(search["urls"]),
                "successful": len(successful),
                "failed": len(failed),
                "duplicates": duplicates,
                "usable": sum(
                    bool(p.get("quality", {}).get("usable"))
                    for p in successful
                ),
            },
            "errors": errors,
            "search": {
                "backend": search["backend"],
                "urls": len(search["urls"]),
            },
        }
