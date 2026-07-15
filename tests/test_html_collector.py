from app.collectors.html_policy import HTMLPolicyCollector
from app.schemas import SourceDefinition


def test_html_collector_filters_using_extra_rules(monkeypatch) -> None:
    html = """
    <html><body>
      <a href="/news/good-post">Introducing Claude Tag</a>
      <a href="/careers/job-post">Careers at Anthropic</a>
      <a href="https://external.example.com/post">External Link</a>
    </body></html>
    """

    class Response:
        text = html

        @staticmethod
        def raise_for_status() -> None:
            return None

    def fake_get(*args, **kwargs):
        assert kwargs["headers"]["User-Agent"]
        assert kwargs["headers"]["Accept-Language"].startswith("zh-CN")
        return Response()

    monkeypatch.setattr("app.collectors.html_policy.httpx.get", fake_get)
    collector = HTMLPolicyCollector()
    source = SourceDefinition(
        id="anthropic-news",
        name="Anthropic News",
        category="ai",
        region="global",
        type="html",
        url="https://www.anthropic.com/news",
        enabled=True,
        priority=10,
        tags=["ai"],
        max_results=10,
        extra={
            "same_domain_only": True,
            "allow_url_keywords": ["/news/"],
            "deny_url_keywords": ["/careers"],
            "allow_title_keywords": ["claude", "introducing"],
        },
    )

    items = collector.collect(source)
    assert len(items) == 1
    assert items[0].title == "Introducing Claude Tag"
