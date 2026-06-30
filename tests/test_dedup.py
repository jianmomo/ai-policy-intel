from datetime import datetime

from app.schemas import CollectedItem
from app.utils.dedup import deduplicate_items


def test_deduplicate_items_by_url_and_title() -> None:
    items = [
        CollectedItem(source_id="a", title="Title A", url="https://example.com/x", published_at=None),
        CollectedItem(source_id="a", title="Title A", url="https://example.com/x", published_at=None),
        CollectedItem(source_id="b", title="Title B", url="https://example.com/y", published_at=None),
    ]
    assert len(deduplicate_items(items)) == 2


def test_deduplicate_items_http_https_for_known_gov_domains() -> None:
    items = [
        CollectedItem(source_id="miit", title="Policy A", url="http://www.miit.gov.cn/xwfb/test.html", published_at=None),
        CollectedItem(source_id="miit", title="Policy A", url="https://www.miit.gov.cn/xwfb/test.html", published_at=None),
    ]
    assert len(deduplicate_items(items)) == 1


def test_deduplicate_near_duplicate_titles_same_day() -> None:
    items = [
        CollectedItem(source_id="openai-news", title="OpenAI 发布 GPT-5 编码模型", url="https://example.com/a", published_at=datetime(2026, 6, 29)),
        CollectedItem(source_id="openai-news", title="OpenAI 发布 GPT-5 编码模型 | 官方博客", url="https://mirror.example.com/b", published_at=datetime(2026, 6, 29)),
    ]
    assert len(deduplicate_items(items)) == 1
