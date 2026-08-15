from .base_connector import BaseConnector
from bs4 import BeautifulSoup
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
import hashlib
import html as html_lib
import ipaddress
import json
import re
import socket
from datetime import datetime
from typing import Any, Optional

import requests

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

DUCKDUCKGO_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"


class WebScraperConnector(BaseConnector):
    """Safe, research-oriented web evidence connector.

    The Research Executor owns parallelism. This connector keeps each
    execute() isolated and returns structured evidence for the synthesizer.
    """

    SEARCH_TIMEOUT = (5, 10)
    PAGE_TIMEOUT = (5, 15)

    MAX_RESULTS = 5
    MAX_REDIRECTS = 3
    MAX_SEARCH_BYTES = 1_000_000
    MAX_HTML_BYTES = 3_000_000
    MAX_PDF_BYTES = 8_000_000

    MAX_TEXT_LENGTH = 8_000
    MAX_COMBINED_TEXT_LENGTH = 40_000
    MIN_TEXT_LENGTH = 80
    MAX_PDF_PAGES = 8
    MAX_BLOCKS = 80
    MAX_AUTHORS = 30

    HTTP_REQUEST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/pdf;q=0.9,text/plain;q=0.8,*/*;q=0.1"
        ),
    }

    _TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term",
        "utm_content", "gclid", "fbclid", "mc_cid", "mc_eid",
    }

    _BLOCK_MARKERS = (
        "access denied", "403 forbidden", "captcha",
        "verify you are human", "checking your browser",
        "just a moment", "request blocked", "temporarily unavailable",
    )

    _NOISE_TAGS = {
        "script", "style", "noscript", "svg", "canvas", "iframe",
        "nav", "footer", "header", "form", "aside",
    }

    _SOURCE_RULES = {
        # Domain -> (type, tier, score)
        "arxiv.org": ("academic", "primary_research", 0.95),
        "openreview.net": ("academic", "primary_research", 0.95),
        "aclanthology.org": ("academic", "primary_research", 0.95),
        "proceedings.iclr.cc": ("academic", "primary_research", 0.95),
        "semanticscholar.org": ("academic", "research_index", 0.88),
        "paperswithcode.com": ("research_index", "research_index", 0.86),
        "ai.google": ("industry_lab", "primary_industry", 0.93),
        "deepmind.google": ("industry_lab", "primary_industry", 0.93),
        "research.google": ("industry_lab", "primary_industry", 0.93),
        "openai.com": ("industry_lab", "primary_industry", 0.93),
        "anthropic.com": ("industry_lab", "primary_industry", 0.93),
        "ai.meta.com": ("industry_lab", "primary_industry", 0.92),
        "microsoft.com": ("industry_lab", "primary_industry", 0.90),
        "hai.stanford.edu": ("research_institution", "institutional", 0.91),
        "cs.stanford.edu": ("research_institution", "institutional", 0.91),
        "mit.edu": ("research_institution", "institutional", 0.91),
        "sciencedirect.com": ("academic_publisher", "publisher", 0.86),
        "nature.com": ("academic_publisher", "publisher", 0.90),
        "science.org": ("academic_publisher", "publisher", 0.90),
        "aimodels.fyi": ("research_aggregator", "secondary_research", 0.80),
    }

    @property
    def name(self):
        return "web_scraper"

    # -------------------------- URL / NETWORK --------------------------

    @classmethod
    def _canonical_url(cls, url: str) -> str:
        try:
            parsed = urlparse(url.strip())
            port = parsed.port
        except (ValueError, TypeError):
            return ""

        if parsed.scheme.lower() not in {"http", "https"}:
            return ""

        hostname = (parsed.hostname or "").lower().rstrip(".")
        if not hostname:
            return ""

        clean_query = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in cls._TRACKING_PARAMS
        ]

        netloc = hostname
        if port:
            default = (
                parsed.scheme.lower() == "http" and port == 80
            ) or (
                parsed.scheme.lower() == "https" and port == 443
            )
            if not default:
                netloc = f"{hostname}:{port}"

        return urlunparse((
            parsed.scheme.lower(), netloc, parsed.path or "/",
            "", urlencode(clean_query, doseq=True), "",
        ))

    @staticmethod
    def _is_public_host(hostname: str) -> tuple[bool, str | None]:
        if not hostname:
            return False, "missing hostname"

        if hostname.lower() in {"localhost", "localhost.localdomain"}:
            return False, "localhost blocked"

        try:
            addresses = {
                info[4][0]
                for info in socket.getaddrinfo(
                    hostname, None, type=socket.SOCK_STREAM
                )
            }
        except socket.gaierror as exc:
            return False, f"DNS resolution failed: {exc}"

        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                return False, f"invalid resolved address: {address}"

            # is_global catches IPv4-mapped IPv6 and other non-public ranges
            # more robustly than checking only a few flags.
            if not ip.is_global:
                return False, f"non-public address blocked: {address}"

        return True, None

    @classmethod
    def _validate_url(cls, url: str) -> tuple[bool, str | None]:
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            return False, f"invalid URL: {exc}"

        if parsed.scheme.lower() not in {"http", "https"}:
            return False, f"unsupported scheme: {parsed.scheme}"

        if parsed.username or parsed.password:
            return False, "URLs containing credentials are blocked"

        return cls._is_public_host(parsed.hostname or "")

    @classmethod
    def _fetch_bytes(
        cls,
        url: str,
        *,
        method: str = "GET",
        data: Optional[dict[str, Any]] = None,
        timeout: tuple[int, int],
        max_bytes: int,
    ) -> dict[str, Any]:
        current_url = cls._canonical_url(url)
        if not current_url:
            return {"ok": False, "error": "invalid URL", "url": url}

        visited = set()

        for _ in range(cls.MAX_REDIRECTS + 1):
            if current_url in visited:
                return {"ok": False, "error": "redirect loop", "url": current_url}
            visited.add(current_url)

            valid, error = cls._validate_url(current_url)
            if not valid:
                return {"ok": False, "error": error, "url": current_url}

            response = None
            try:
                response = requests.request(
                    method, current_url, headers=cls.HTTP_REQUEST_HEADERS,
                    data=data, timeout=timeout, allow_redirects=False, stream=True,
                )

                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    response.close()
                    if not location:
                        return {"ok": False, "error": "redirect without Location header",
                                "url": current_url}

                    next_url = cls._canonical_url(urljoin(current_url, location))
                    valid, error = cls._validate_url(next_url)
                    if not valid:
                        return {"ok": False, "error": f"unsafe redirect blocked: {error}",
                                "url": next_url}

                    current_url = next_url
                    method, data = "GET", None
                    continue

                if response.status_code != 200:
                    return {
                        "ok": False,
                        "error": f"HTTP {response.status_code}",
                        "url": current_url,
                        "status_code": response.status_code,
                    }

                content_length = response.headers.get("Content-Length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    return {"ok": False, "error": f"response exceeds limit ({max_bytes} bytes)",
                            "url": current_url}

                chunks, total = [], 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        return {"ok": False, "error": f"response exceeds limit ({max_bytes} bytes)",
                                "url": current_url}
                    chunks.append(chunk)

                return {
                    "ok": True,
                    "url": current_url,
                    "status_code": response.status_code,
                    "content_type": response.headers.get("Content-Type", "")
                    .split(";", 1)[0].strip().lower(),
                    "content": b"".join(chunks),
                }

            except requests.RequestException as exc:
                return {"ok": False, "error": str(exc), "url": current_url}
            finally:
                if response is not None:
                    response.close()

        return {"ok": False, "error": "too many redirects", "url": current_url}

    # ------------------------------- SEARCH ----------------------------

    def _fetch_search_urls(self, query: str) -> dict[str, Any]:
        result = self._fetch_bytes(
            DUCKDUCKGO_SEARCH_ENDPOINT,
            method="POST",
            data={"q": query},
            timeout=self.SEARCH_TIMEOUT,
            max_bytes=self.MAX_SEARCH_BYTES,
        )

        if not result["ok"]:
            return {"ok": False, "urls": [],
                    "error": f"search request failed: {result['error']}"}

        if result["content_type"] and "html" not in result["content_type"]:
            return {"ok": False, "urls": [],
                    "error": f"unexpected search content type: {result['content_type']}"}

        soup = BeautifulSoup(
            result["content"].decode("utf-8", errors="replace"), "html.parser"
        )
        candidates = []

        for tag in soup.select(".result__a"):
            href = tag.get("href")
            title = tag.get_text(" ", strip=True)
            if not href:
                continue

            canonical = self._canonical_url(href)
            if not canonical:
                continue

            valid, error = self._validate_url(canonical)
            if valid:
                candidates.append({"url": canonical, "title": title})

        unique = {}
        for item in candidates:
            unique.setdefault(item["url"], item)

        urls = list(unique.values())[: self.MAX_RESULTS]
        return {
            "ok": bool(urls),
            "urls": urls,
            "error": None if urls else "no usable search results",
        }

    # ------------------------- EXTRACTION HELPERS ----------------------

    @staticmethod
    def _clean_text(text: str) -> str:
        text = html_lib.unescape(text or "")
        text = text.replace("\xa0", " ")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()

    @classmethod
    def _normalize_for_hash(cls, text: str) -> str:
        # Remove low-information navigation/boilerplate before content hashing.
        text = cls._clean_text(text).lower()
        text = re.sub(r"\b(view pdf|html|experimental|tex source|"
                      r"google scholar|semantic scholar|arxivlabs)\b", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @classmethod
    def _text_quality(cls, text: str) -> dict[str, Any]:
        stripped = cls._clean_text(text)
        if not stripped:
            return {"usable": False, "score": 0.0, "reason": "empty text"}

        printable = sum(ch.isprintable() or ch in "\n\t" for ch in stripped)
        printable_ratio = printable / max(len(stripped), 1)
        if printable_ratio < 0.85:
            return {"usable": False, "score": 0.0,
                    "reason": "text appears binary/corrupted"}

        lower = re.sub(r"\s+", " ", stripped.lower())
        for marker in cls._BLOCK_MARKERS:
            if marker in lower and len(stripped) < 2000:
                return {"usable": False, "score": 0.0,
                        "reason": f"likely blocked page: {marker}"}

        # Penalize pages that contain mostly repeated UI-like fragments.
        words = re.findall(r"\b[\w'-]+\b", lower)
        unique_ratio = len(set(words)) / max(len(words), 1)
        length_score = min(len(stripped) / 2000, 1.0)
        score = 0.4 + 0.35 * length_score + 0.15 * printable_ratio + 0.10 * unique_ratio

        usable = len(stripped) >= cls.MIN_TEXT_LENGTH and unique_ratio >= 0.20
        return {
            "usable": usable,
            "score": round(min(score, 1.0), 3),
            "reason": None if usable else "insufficient or low-information text",
        }

    @staticmethod
    def _first_meta(soup: BeautifulSoup, *names: str) -> str:
        for name in names:
            tag = soup.find("meta", attrs={"name": name}) or soup.find(
                "meta", attrs={"property": name}
            )
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    @classmethod
    def _year(cls, value: str) -> Optional[int]:
        match = re.search(r"\b(19|20)\d{2}\b", value or "")
        return int(match.group()) if match else None

    @classmethod
    def _authors_from_text(cls, value: str) -> list[str]:
        if not value:
            return []
        parts = re.split(r",|;|\band\b", value, flags=re.I)
        result = []
        for part in parts:
            name = re.sub(r"\s+", " ", part).strip()
            if 2 <= len(name) <= 120 and len(name.split()) <= 12:
                result.append(name)
        return list(dict.fromkeys(result))[: cls.MAX_AUTHORS]

    @classmethod
    def _extract_research_metadata(cls, soup: BeautifulSoup, title: str) -> dict[str, Any]:
        """Extract common scholarly metadata without requiring a site-specific API."""
        abstract = (
            cls._first_meta(
                soup,
                "citation_abstract", "dc.description", "description",
                "og:description", "twitter:description",
            )
        )

        author_values = []
        for name in ("citation_author", "author", "dc.creator"):
            author_values.extend(
                tag.get("content", "").strip()
                for tag in soup.find_all("meta", attrs={"name": name})
                if tag.get("content")
            )

        # Scholarly JSON-LD is another useful path.
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            try:
                data = json.loads(raw)
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if not abstract:
                    abstract = str(item.get("abstract") or item.get("description") or "")
                authors = item.get("author")
                if isinstance(authors, dict):
                    authors = [authors]
                if isinstance(authors, list):
                    for author in authors:
                        if isinstance(author, dict) and author.get("name"):
                            author_values.append(str(author["name"]))
                        elif isinstance(author, str):
                            author_values.append(author)

        date_value = (
            cls._first_meta(
                soup,
                "citation_publication_date", "citation_date",
                "datePublished", "article:published_time",
                "dc.date", "date",
            )
        )

        if not abstract:
            # Site-independent fallback: look for an explicit abstract heading.
            for heading in soup.find_all(re.compile(r"^h[1-6]$")):
                if "abstract" in heading.get_text(" ", strip=True).lower():
                    collected = []
                    for node in heading.find_all_next(["p", "div"], limit=8):
                        text = node.get_text(" ", strip=True)
                        if text:
                            collected.append(text)
                        if len(" ".join(collected)) > 3000:
                            break
                    abstract = " ".join(collected)[:3000]
                    if abstract:
                        break

        return {
            "kind": "research_paper" if (abstract or author_values or "arxiv.org" in title.lower())
            else "web_page",
            "year": cls._year(date_value),
            "authors": cls._authors_from_text("; ".join(author_values)),
            "abstract": cls._clean_text(abstract)[:3000] or None,
        }

    # -------------------------- HTML EXTRACTION ------------------------

    def _extract_arxiv_html(self, soup: BeautifulSoup) -> Optional[dict[str, Any]]:
        """ArXiv-specific path; handles metadata first and paper HTML when present."""
        title = (
            self._first_meta(soup, "citation_title", "og:title")
            or (soup.title.get_text(" ", strip=True) if soup.title else "")
        )
        abstract = self._first_meta(
            soup, "citation_abstract", "description", "og:description"
        )

        # Modern arXiv abstract pages often expose the abstract in blockquote.
        abs_node = soup.select_one(".abstract, blockquote.abstract")
        if abs_node:
            abstract = abstract or abs_node.get_text(" ", strip=True)

        body_candidates = []
        for selector in (
            "article", "main", "#content", ".ltx_document",
            ".ltx_page_main", ".ltx_abstract",
        ):
            body_candidates.extend(soup.select(selector))

        root = body_candidates[0] if body_candidates else soup.body or soup
        for tag in root.find_all(self._NOISE_TAGS):
            tag.decompose()

        # Prefer headings + paragraphs + list items + table captions/cells.
        nodes = root.find_all([
            "h1", "h2", "h3", "h4", "h5", "h6",
            "p", "li", "blockquote", "caption", "td", "th",
        ])

        chunks = []
        for node in nodes[: self.MAX_BLOCKS]:
            text = self._clean_text(node.get_text(" ", strip=True))
            if text and len(text) >= 3:
                chunks.append(text)

        text = "\n".join(dict.fromkeys(chunks))
        if abstract and abstract not in text:
            text = f"Abstract: {abstract}\n{text}"

        return {
            "text": text[: self.MAX_TEXT_LENGTH],
            "title": self._clean_text(title)[:300],
            "metadata": self._extract_research_metadata(soup, title),
            "path": "arxiv_html",
        }

    def _extract_html(self, content: bytes, url: str = "") -> dict[str, Any]:
        html = content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        title = (
            self._first_meta(soup, "citation_title", "og:title", "twitter:title")
            or (soup.title.get_text(" ", strip=True) if soup.title else "")
        )

        # Strongest site-specific path first.
        hostname = (urlparse(url).hostname or "").lower()
        if hostname == "arxiv.org":
            arxiv = self._extract_arxiv_html(soup)
            if arxiv:
                quality = self._text_quality(arxiv["text"])
                if quality["usable"]:
                    return {
                        "ok": True,
                        "text": arxiv["text"],
                        "title": arxiv["title"] or title[:300],
                        "content_type": "text/html",
                        "quality": quality,
                        "metadata": arxiv["metadata"],
                        "extraction": arxiv["path"],
                    }

        # Generic DOM cleanup.
        for tag in soup.find_all(self._NOISE_TAGS):
            tag.decompose()

        # Remove obvious cookie/ad/modal elements by class/id heuristics.
        for tag in soup.find_all(True):
            marker = f"{tag.get('id', '')} {' '.join(tag.get('class', []))}".lower()
            if re.search(r"(cookie|consent|advert|banner|popup|modal|subscribe)", marker):
                tag.decompose()

        research_meta = self._extract_research_metadata(soup, title)

        # Extraction path 1: semantic content container.
        roots = []
        for selector in (
            "article", "main", '[role="main"]',
            ".article-body", ".article-content", ".post-content",
            ".entry-content", ".content", "#content",
        ):
            roots.extend(soup.select(selector))

        roots.append(soup.body or soup)
        best_text = ""
        best_chunks = []

        for root in roots[:10]:
            nodes = root.find_all([
                "h1", "h2", "h3", "h4", "h5", "h6",
                "p", "li", "blockquote", "figcaption",
            ])
            chunks = []
            for node in nodes[: self.MAX_BLOCKS]:
                text = self._clean_text(node.get_text(" ", strip=True))
                if text and len(text) >= 3:
                    chunks.append(text)
            candidate = "\n".join(dict.fromkeys(chunks))
            if len(candidate) > len(best_text):
                best_text, best_chunks = candidate, chunks

        # Extraction path 2: text-rich div/section fallback.
        if len(best_text) < self.MIN_TEXT_LENGTH:
            candidates = []
            for node in soup.find_all(["section", "div"]):
                text = self._clean_text(node.get_text(" ", strip=True))
                if 100 <= len(text) <= self.MAX_TEXT_LENGTH * 2:
                    density = len(node.find_all(["p", "li"]))
                    candidates.append((density, len(text), text))
            candidates.sort(reverse=True)
            if candidates:
                best_text = candidates[0][2]

        # Extraction path 3: visible document text fallback.
        if len(best_text) < self.MIN_TEXT_LENGTH:
            best_text = self._clean_text(soup.get_text(" ", strip=True))

        if research_meta["abstract"] and research_meta["abstract"] not in best_text:
            best_text = f"Abstract: {research_meta['abstract']}\n{best_text}"

        best_text = best_text[: self.MAX_TEXT_LENGTH]
        quality = self._text_quality(best_text)

        return {
            "ok": quality["usable"],
            "text": best_text if quality["usable"] else "",
            "title": self._clean_text(title)[:300],
            "content_type": "text/html",
            "quality": quality,
            "metadata": research_meta,
            "extraction": "generic_multi_path",
        }

    # --------------------------- PDF EXTRACTION ------------------------

    def _extract_pdf(self, content: bytes) -> dict[str, Any]:
        if PdfReader is None:
            return {
                "ok": False, "text": "", "title": "",
                "content_type": "application/pdf",
                "quality": {"usable": False, "score": 0.0,
                            "reason": "pypdf is not installed"},
                "metadata": {"kind": "research_paper", "year": None,
                             "authors": [], "abstract": None},
                "extraction": "pdf_unavailable",
            }

        try:
            import io
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                return {
                    "ok": False, "text": "", "title": "",
                    "content_type": "application/pdf",
                    "quality": {"usable": False, "score": 0.0,
                                "reason": "encrypted PDF"},
                    "metadata": {"kind": "research_paper", "year": None,
                                 "authors": [], "abstract": None},
                    "extraction": "pdf",
                }

            chunks = []
            for page in reader.pages[: self.MAX_PDF_PAGES]:
                text = self._clean_text(page.extract_text() or "")
                if text:
                    chunks.append(text)

            text = "\n".join(chunks)[: self.MAX_TEXT_LENGTH]
            quality = self._text_quality(text)

            metadata = {
                "kind": "research_paper" if text else "document",
                "year": self._year(text[:2000]),
                "authors": [],
                "abstract": None,
            }

            # Lightweight PDF metadata path.
            meta = reader.metadata
            if meta:
                title = self._clean_text(str(meta.get("/Title") or ""))
                author = self._clean_text(str(meta.get("/Author") or ""))
            else:
                title, author = "", ""

            return {
                "ok": quality["usable"],
                "text": text if quality["usable"] else "",
                "title": title[:300],
                "content_type": "application/pdf",
                "quality": quality,
                "metadata": metadata,
                "pdf_metadata": {"author": author or None},
                "extraction": "pdf_pypdf",
            }

        except Exception as exc:
            return {
                "ok": False, "text": "", "title": "",
                "content_type": "application/pdf",
                "quality": {"usable": False, "score": 0.0,
                            "reason": f"PDF extraction failed: {exc}"},
                "metadata": {"kind": "research_paper", "year": None,
                             "authors": [], "abstract": None},
                "extraction": "pdf_pypdf",
            }

    # -------------------------- SOURCE METADATA ------------------------

    @classmethod
    def _source_metadata(cls, url: str) -> dict[str, Any]:
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        base = cls._SOURCE_RULES.get(hostname)

        if base:
            source_type, tier, score = base
        elif hostname.endswith((".edu", ".ac.uk", ".ac.in")):
            source_type, tier, score = "academic", "academic", 0.84
        elif hostname.endswith((".gov", ".gov.in", ".nic.in")):
            source_type, tier, score = "government", "primary_official", 0.90
        else:
            source_type, tier, score = "general_web", "unclassified", 0.55

        return {
            "domain": hostname,
            "type": source_type,
            "tier": tier,
            "score": score,
            "heuristic": True,
        }

    # ------------------------- DOCUMENT IDENTITY -----------------------

    @classmethod
    def _document_id(cls, canonical_url: str, text: str) -> tuple[str, str]:
        url_id = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
        normalized = cls._normalize_for_hash(text)
        content_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return url_id, content_id

    @classmethod
    def _similar_content_key(cls, text: str) -> Optional[str]:
        normalized = cls._normalize_for_hash(text)
        if len(normalized) < 250:
            return None

        # Hash only a bounded prefix to avoid memory growth.
        return hashlib.sha256(normalized[:12_000].encode("utf-8")).hexdigest()[:16]

    # ----------------------------- SCRAPE ------------------------------

    def _failed_page(self, requested_url: str, final_url: str, title: str,
                     source_url: str, content_type: str, reason: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "url": requested_url,
            "final_url": final_url,
            "title": title[:300],
            "text": "",
            "content_type": content_type,
            "source": self._source_metadata(source_url),
            "quality": {"usable": False, "score": 0.0, "reason": reason},
            "evidence": {
                "kind": "unknown", "year": None, "authors": [], "abstract": None
            },
            "document_id": None,
            "content_id": None,
            "error": reason,
        }

    def _scrape_page(self, search_item: dict[str, Any]) -> dict[str, Any]:
        requested_url = search_item["url"]

        result = self._fetch_bytes(
            requested_url,
            timeout=self.PAGE_TIMEOUT,
            max_bytes=max(self.MAX_HTML_BYTES, self.MAX_PDF_BYTES),
        )

        if not result["ok"]:
            return self._failed_page(
                requested_url, result.get("url", requested_url),
                search_item.get("title", ""), requested_url, "", result["error"]
            )

        final_url = self._canonical_url(result["url"])
        content_type = result["content_type"]
        content = result["content"]

        is_pdf = self._looks_like_pdf(content) or content_type == "application/pdf"

        if is_pdf:
            if len(content) > self.MAX_PDF_BYTES:
                return self._failed_page(
                    requested_url, final_url, search_item.get("title", ""),
                    final_url, content_type, "PDF exceeds size limit"
                )
            extracted = self._extract_pdf(content)

        elif (
            "html" in content_type or "xhtml" in content_type
            or self._looks_like_html(content)
        ):
            if len(content) > self.MAX_HTML_BYTES:
                return self._failed_page(
                    requested_url, final_url, search_item.get("title", ""),
                    final_url, content_type, "HTML response exceeds size limit"
                )
            extracted = self._extract_html(content, final_url)

        elif content_type.startswith("text/plain"):
            text = self._clean_text(content.decode("utf-8", errors="replace"))[:self.MAX_TEXT_LENGTH]
            quality = self._text_quality(text)
            extracted = {
                "ok": quality["usable"],
                "text": text if quality["usable"] else "",
                "title": search_item.get("title", ""),
                "content_type": "text/plain",
                "quality": quality,
                "metadata": {"kind": "text", "year": None, "authors": [], "abstract": None},
                "extraction": "plain_text",
            }
        else:
            extracted = {
                "ok": False, "text": "", "title": search_item.get("title", ""),
                "content_type": content_type or "unknown",
                "quality": {"usable": False, "score": 0.0,
                            "reason": "unsupported content type"},
                "metadata": {"kind": "unknown", "year": None,
                             "authors": [], "abstract": None},
                "extraction": "unsupported",
            }

        text = extracted.get("text", "")
        title = (extracted.get("title") or search_item.get("title", "")).strip()
        metadata = extracted.get("metadata") or {
            "kind": "unknown", "year": None, "authors": [], "abstract": None
        }

        document_id = content_id = None
        if extracted.get("ok"):
            document_id, content_id = self._document_id(final_url, text)

        return {
            "status": "success" if extracted.get("ok") else "failed",
            "url": requested_url,
            "final_url": final_url,
            "title": title[:300],
            "text": text,
            "source": self._source_metadata(final_url),
            "quality": extracted["quality"],
            "evidence": {
                "kind": metadata.get("kind", "unknown"),
                "year": metadata.get("year"),
                "authors": metadata.get("authors", [])[:self.MAX_AUTHORS],
                "abstract": metadata.get("abstract"),
            },
            "document_id": document_id,
            "content_id": content_id,
            "content_type": extracted.get("content_type", content_type),
            "extraction": extracted.get("extraction"),
            "error": None if extracted.get("ok") else extracted["quality"].get("reason"),
        }

    # ------------------------------ PUBLIC -----------------------------

    def execute(self, search_query: str):
        query = str(search_query).strip()

        base_summary = {
            "found": 0,
            "successful": 0,
            "failed": 0,
            "duplicates": 0,
            "usable": 0,
        }

        if not query:
            return {
                "ok": False, "status": "failed", "query": query,
                "connector": self.name, "results": [],
                "web_scraped_text": "", "pages": [],
                "summary": base_summary,
                "errors": ["search query is empty"],
            }

        search = self._fetch_search_urls(query)
        if not search["ok"]:
            return {
                "ok": False, "status": "failed", "query": query,
                "connector": self.name, "results": [],
                "web_scraped_text": "", "pages": [],
                "summary": base_summary,
                "errors": [search["error"]],
            }

        pages = []
        seen_url = set()
        seen_content = {}

        for item in search["urls"]:
            if item["url"] in seen_url:
                continue
            seen_url.add(item["url"])

            page = self._scrape_page(item)

            # Only deduplicate sufficiently informative successful content.
            # Failed/very short extraction must never suppress another source.
            if page["status"] == "success":
                key = self._similar_content_key(page["text"])
                if key and key in seen_content:
                    page["status"] = "duplicate"
                    page["error"] = (
                        f"duplicate content of {seen_content[key]}"
                    )
                elif key:
                    seen_content[key] = page["url"]

            pages.append(page)

        successful = [p for p in pages if p["status"] == "success"]
        failed = [p for p in pages if p["status"] == "failed"]
        duplicates = [p for p in pages if p["status"] == "duplicate"]

        results = successful
        combined = "\n\n".join(
            f"{p['title']}\n{p['text']}" if p["title"] else p["text"]
            for p in results
        )[: self.MAX_COMBINED_TEXT_LENGTH]

        if successful and not failed and not duplicates:
            status = "success"
        elif successful:
            status = "partial"
        else:
            status = "failed"

        errors = [
            f"{p['url']} -> {p['error']}"
            for p in pages if p.get("error")
        ]

        summary = {
            "found": len(search["urls"]),
            "successful": len(successful),
            "failed": len(failed),
            "duplicates": len(duplicates),
            "usable": sum(
                bool(p.get("quality", {}).get("usable"))
                for p in successful
            ),
        }

        # `pages` remains as a compatibility alias. `results` is canonical.
        return {
            "ok": bool(successful),
            "status": status,
            "query": query,
            "connector": self.name,
            "results": results,
            "web_scraped_text": combined,
            "pages": pages,
            "summary": summary,
            "errors": errors,
        }

    # ----------------------- CONTENT DETECTION -------------------------

    @staticmethod
    def _looks_like_pdf(content: bytes) -> bool:
        return content[:5] == b"%PDF-"

    @staticmethod
    def _looks_like_html(content: bytes) -> bool:
        head = content[:4096].lstrip().lower()
        return (
            b"<html" in head or b"<!doctype html" in head
            or b"<body" in head or b"<article" in head
        )
        
  