from datetime import datetime
from urllib.parse import urlencode

import httpx
from dateutil.parser import isoparse

from app.collectors.base import BaseCollector
from app.config import settings
from app.schemas import CollectedItem, SourceDefinition


class GitHubCollector(BaseCollector):
    def _build_url(self, source: SourceDefinition) -> str:
        if source.query:
            params = {
                "q": source.query,
                "sort": source.extra.get("sort", "updated"),
                "order": source.extra.get("order", "desc"),
                "per_page": str(source.max_results),
            }
            return f"https://api.github.com/search/repositories?{urlencode(params)}"
        return source.url

    def collect(self, source: SourceDefinition) -> list[CollectedItem]:
        headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        try:
            response = httpx.get(self._build_url(source), headers=headers, timeout=20.0)
            response.raise_for_status()
            payload = response.json()
            repos = payload.get("items", [])[: source.max_results]
            items: list[CollectedItem] = []
            for repo in repos:
                published = None
                if repo.get("pushed_at"):
                    try:
                        published = isoparse(repo["pushed_at"]).replace(tzinfo=None)
                    except Exception:
                        published = datetime.utcnow()
                content = " ".join(
                    filter(
                        None,
                        [
                            repo.get("description") or "",
                            " ".join(repo.get("topics", []) or []),
                            repo.get("language") or "",
                        ],
                    )
                )
                items.append(
                    CollectedItem(
                        source_id=source.id,
                        title=repo.get("full_name", ""),
                        url=repo.get("html_url", ""),
                        published_at=published or datetime.utcnow(),
                        raw_summary=repo.get("description") or "",
                        raw_content=content,
                        region=source.region,
                        tags=list(source.tags),
                    )
                )
            return items
        except Exception:
            if not settings.enable_mock_collectors:
                raise
            return [
                CollectedItem(
                    source_id=source.id,
                    title="mock/ai-policy-intel-helper",
                    url="https://github.com/mock/ai-policy-intel-helper",
                    published_at=datetime.utcnow(),
                    raw_summary="Mock GitHub item used when remote access or token is unavailable.",
                    raw_content="Mock GitHub item used when remote access or token is unavailable.",
                    region=source.region,
                    tags=list(source.tags),
                )
            ]
