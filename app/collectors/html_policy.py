from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.schemas import CollectedItem, SourceDefinition


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

BLOCKED_BITS = ("\u5907\u6848", "\u7f51\u7ad9\u5730\u56fe", "\u90ae\u7bb1", "english", "\u8054\u7cfb\u6211\u4eec")


class HTMLPolicyCollector(BaseCollector):
    def _headers_for(self, source: SourceDefinition) -> dict[str, str]:
        headers = dict(DEFAULT_HEADERS)
        extra_headers = source.extra.get("headers", {})
        if isinstance(extra_headers, dict):
            headers.update({str(key): str(value) for key, value in extra_headers.items()})
        return headers

    def collect(self, source: SourceDefinition) -> list[CollectedItem]:
        response = httpx.get(
            source.url,
            headers=self._headers_for(source),
            timeout=20.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items: list[CollectedItem] = []
        seen: set[str] = set()
        allow_url_keywords = [str(value).lower() for value in source.extra.get("allow_url_keywords", [])]
        deny_url_keywords = [str(value).lower() for value in source.extra.get("deny_url_keywords", [])]
        allow_title_keywords = [str(value).lower() for value in source.extra.get("allow_title_keywords", [])]
        deny_title_keywords = [str(value).lower() for value in source.extra.get("deny_title_keywords", [])]
        same_domain_only = bool(source.extra.get("same_domain_only", False))
        source_domain = urlparse(source.url).netloc.lower()

        for anchor in soup.find_all("a", href=True):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            href = urljoin(source.url, anchor["href"])
            lowered_title = title.lower()
            lowered_href = href.lower()

            if href.startswith(("javascript:", "mailto:")):
                continue
            if len(title) < 8 or href in seen:
                continue
            if any(bit.lower() in lowered_title for bit in BLOCKED_BITS):
                continue
            if any(bit in lowered_title for bit in deny_title_keywords):
                continue
            if "beian.miit.gov.cn" in lowered_href:
                continue
            if same_domain_only and urlparse(href).netloc.lower() != source_domain:
                continue
            if allow_url_keywords and not any(keyword in lowered_href for keyword in allow_url_keywords):
                continue
            if deny_url_keywords and any(keyword in lowered_href for keyword in deny_url_keywords):
                continue
            if allow_title_keywords and not any(keyword in lowered_title for keyword in allow_title_keywords):
                continue

            seen.add(href)
            items.append(
                CollectedItem(
                    source_id=source.id,
                    title=title,
                    url=href,
                    published_at=datetime.utcnow(),
                    raw_summary="",
                    raw_content=title,
                    region=source.region,
                    tags=list(source.tags),
                )
            )
            if len(items) >= source.max_results:
                break
        return items
