from datetime import datetime

import feedparser

from app.collectors.base import BaseCollector
from app.schemas import CollectedItem, SourceDefinition


class RSSCollector(BaseCollector):
    @staticmethod
    def _matches_any(text: str, keywords: list[str]) -> bool:
        lowered = (text or '').lower()
        return any(str(keyword).lower() in lowered for keyword in keywords)

    def _passes_filters(self, source: SourceDefinition, title: str, summary: str, link: str) -> bool:
        allow_keywords = [str(value) for value in source.extra.get('allow_keywords', [])]
        deny_keywords = [str(value) for value in source.extra.get('deny_keywords', [])]
        allow_title_keywords = [str(value) for value in source.extra.get('allow_title_keywords', [])]
        deny_title_keywords = [str(value) for value in source.extra.get('deny_title_keywords', [])]
        allow_url_keywords = [str(value) for value in source.extra.get('allow_url_keywords', [])]
        deny_url_keywords = [str(value) for value in source.extra.get('deny_url_keywords', [])]

        lowered_title = title.lower()
        lowered_summary = summary.lower()
        lowered_link = link.lower()
        combined = f'{lowered_title} {lowered_summary}'

        if allow_keywords and not self._matches_any(combined, allow_keywords):
            return False
        if deny_keywords and self._matches_any(combined, deny_keywords):
            return False
        if allow_title_keywords and not self._matches_any(lowered_title, allow_title_keywords):
            return False
        if deny_title_keywords and self._matches_any(lowered_title, deny_title_keywords):
            return False
        if allow_url_keywords and not self._matches_any(lowered_link, allow_url_keywords):
            return False
        if deny_url_keywords and self._matches_any(lowered_link, deny_url_keywords):
            return False
        return True

    def collect(self, source: SourceDefinition) -> list[CollectedItem]:
        parsed = feedparser.parse(source.url)
        items: list[CollectedItem] = []
        for entry in parsed.entries:
            title = getattr(entry, 'title', '').strip()
            link = getattr(entry, 'link', '').strip()
            summary = getattr(entry, 'summary', '')
            if not title or not link:
                continue
            if not self._passes_filters(source, title, summary, link):
                continue

            published = None
            if getattr(entry, 'published_parsed', None):
                published = datetime(*entry.published_parsed[:6])
            items.append(
                CollectedItem(
                    source_id=source.id,
                    title=title,
                    url=link,
                    published_at=published,
                    raw_summary=summary,
                    raw_content=summary,
                    region=source.region,
                    tags=list(source.tags),
                )
            )
            if len(items) >= source.max_results:
                break
        return items
