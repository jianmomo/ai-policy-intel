from types import SimpleNamespace

from app.collectors.arxiv import ArxivCollector
from app.collectors.github import GitHubCollector
from app.collectors.rss import RSSCollector
from app.schemas import SourceDefinition


def test_github_collector_builds_query_url() -> None:
    collector = GitHubCollector()
    source = SourceDefinition(
        id='g',
        name='GitHub',
        category='ai',
        region='global',
        type='github',
        url='https://api.github.com/search/repositories',
        enabled=True,
        priority=1,
        query='llm in:name',
        max_results=5,
        extra={'sort': 'updated', 'order': 'desc'},
    )
    url = collector._build_url(source)
    assert 'api.github.com/search/repositories' in url
    assert 'llm+in%3Aname' in url
    assert 'per_page=5' in url


def test_arxiv_collector_builds_query_url() -> None:
    collector = ArxivCollector()
    source = SourceDefinition(
        id='a',
        name='arXiv',
        category='ai',
        region='global',
        type='arxiv',
        url='https://export.arxiv.org/api/query',
        enabled=True,
        priority=1,
        query='cat:cs.AI',
        max_results=7,
        extra={'sortBy': 'submittedDate', 'sortOrder': 'descending'},
    )
    url = collector._build_url(source)
    assert 'export.arxiv.org/api/query' in url
    assert 'search_query=cat%3Acs.AI' in url
    assert 'max_results=7' in url


def test_rss_collector_filters_and_respects_max_results(monkeypatch) -> None:
    collector = RSSCollector()
    source = SourceDefinition(
        id='wechat-ai-demo',
        name='WeChat AI Demo',
        category='ai',
        region='cn',
        type='rss',
        url='https://example.com/feed.xml',
        enabled=True,
        priority=3,
        tags=['ai', 'wechat'],
        max_results=2,
        extra={
            'allow_keywords': ['ai', 'large model'],
            'deny_title_keywords': ['registration', 'hiring'],
        },
    )

    parsed = SimpleNamespace(
        entries=[
            SimpleNamespace(title='AI Agent progress', link='https://example.com/1', summary='latest AI research'),
            SimpleNamespace(title='Event registration is open', link='https://example.com/2', summary='AI summit'),
            SimpleNamespace(title='Large model training', link='https://example.com/3', summary='multimodal model update'),
            SimpleNamespace(title='General tech news', link='https://example.com/4', summary='not related'),
        ]
    )

    monkeypatch.setattr('app.collectors.rss.feedparser.parse', lambda url: parsed)
    items = collector.collect(source)

    assert len(items) == 2
    assert items[0].title == 'AI Agent progress'
    assert items[1].title == 'Large model training'
