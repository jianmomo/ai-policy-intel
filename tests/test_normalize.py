from app.schemas import CollectedItem
from app.utils.normalize import normalize_item, normalize_url


def test_normalize_url_removes_trailing_slash() -> None:
    assert normalize_url("HTTPS://Example.com/path/") == "https://example.com/path"


def test_normalize_url_upgrades_known_gov_domains_to_https() -> None:
    assert normalize_url("http://www.miit.gov.cn/xwfb/test.html") == "https://www.miit.gov.cn/xwfb/test.html"


def test_normalize_item_cleans_spaces() -> None:
    item = CollectedItem(source_id="x", title="  hello   world ", url="https://Example.com/a/", published_at=None)
    normalized = normalize_item(item)
    assert normalized.title == "hello world"
    assert normalized.url == "https://example.com/a"
