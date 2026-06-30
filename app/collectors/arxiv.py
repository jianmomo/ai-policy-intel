from datetime import datetime
from urllib.parse import urlencode

import feedparser
from dateutil.parser import parse as parse_datetime

from app.collectors.base import BaseCollector
from app.config import settings
from app.schemas import CollectedItem, SourceDefinition


class ArxivCollector(BaseCollector):
    def _build_url(self, source: SourceDefinition) -> str:
        if source.query:
            params = {
                "search_query": source.query,
                "start": source.extra.get("start", "0"),
                "max_results": str(source.max_results),
                "sortBy": source.extra.get("sortBy", "submittedDate"),
                "sortOrder": source.extra.get("sortOrder", "descending"),
            }
            return f"https://export.arxiv.org/api/query?{urlencode(params)}"
        return source.url

    def collect(self, source: SourceDefinition) -> list[CollectedItem]:
        try:
            parsed = feedparser.parse(self._build_url(source))
            items: list[CollectedItem] = []
            for entry in parsed.entries[: source.max_results]:
                published = None
                if getattr(entry, "published", None):
                    try:
                        published = parse_datetime(entry.published).replace(tzinfo=None)
                    except Exception:
                        published = datetime.utcnow()
                tag_terms = []
                if isinstance(getattr(entry, "tags", []), list):
                    for tag in entry.tags:
                        if isinstance(tag, dict):
                            term = tag.get("term")
                            if term:
                                tag_terms.append(term)
                        else:
                            tag_terms.append(str(tag))
                items.append(
                    CollectedItem(
                        source_id=source.id,
                        title=getattr(entry, "title", "").strip(),
                        url=getattr(entry, "link", "").strip(),
                        published_at=published or datetime.utcnow(),
                        raw_summary=getattr(entry, "summary", ""),
                        raw_content=" ".join(
                            filter(
                                None,
                                [
                                    getattr(entry, "summary", ""),
                                    " ".join(tag_terms),
                                ],
                            )
                        ),
                        region=source.region,
                        tags=list(source.tags),
                    )
                )
            if items:
                return items
        except Exception:
            pass

        if not settings.enable_mock_collectors:
            return []

        return [
            CollectedItem(
                source_id=source.id,
                title="Mock arXiv breakthrough on efficient multimodal training",
                url="https://arxiv.org/abs/mock",
                published_at=datetime.utcnow(),
                raw_summary="Mock arXiv placeholder item for offline validation.",
                raw_content="Mock arXiv placeholder item for offline validation.",
                region=source.region,
                tags=list(source.tags),
            )
        ]
