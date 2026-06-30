from app.delivery.emailer import (
    _ai_item,
    _build_digest_messages,
    _format_message,
    _looks_english,
    _policy_item,
    telegram_delivery_configured,
)
from app.db.models import Item, Source


def _item(category: str, source_id: str, region: str = 'global', subcategory: str = '', title: str = 'Introducing Claude Opus 4.8 for long-running coding tasks') -> Item:
    row = Item()
    row.category = category
    row.source_id = source_id
    row.region = region
    row.subcategory = subcategory
    row.title = title
    row.summary = 'This release improves long-horizon coding and agent reliability for production deployments.'
    row.raw_content = ''
    row.url = 'https://example.com/article?x=1&y=2'
    row.score = 8.6
    return row


def test_ai_bucket_detection() -> None:
    assert _ai_item(_item('AI-Industry', 'openai-news', subcategory='Official-AI'))
    assert _ai_item(_item('Unclassified', 'wechat-ai-jiqizhixin', region='cn'))
    assert not _ai_item(_item('Policy-Central', 'gov-policy', region='cn'))


def test_policy_bucket_detection() -> None:
    assert _policy_item(_item('Policy-Central', 'gov-policy', region='cn'))
    assert _policy_item(_item('Unclassified', 'wechat-policy-infosec', region='cn'))
    assert not _policy_item(_item('AI-Research', 'arxiv-cs-ai'))


def test_looks_english() -> None:
    assert _looks_english('Introducing Claude Opus 4.8 for long-running coding tasks')
    assert not _looks_english('\u56fd\u52a1\u9662\u5173\u4e8e\u5370\u53d1\u89c4\u5212\u7684\u901a\u77e5')


def test_format_message_escapes_html() -> None:
    row = _item('AI-Industry', 'openai-news', subcategory='Official-AI,Model')
    row.title = 'OpenAI <Preview> & Launch'
    row.summary = 'Model update with <b>unsafe</b> html.'
    source = Source(
        id='openai-news',
        name='OpenAI News',
        category='AI-Industry',
        region='global',
        type='rss',
        url='https://example.com',
        enabled=True,
        priority=5,
        tags='ai',
    )

    title = 'AI\u60c5\u62a5\u65e5\u62a5'
    message = _format_message(title, [row], {'openai-news': source}, {}, kind='ai', daily=True)

    assert '<b>AI\u60c5\u62a5\u65e5\u62a5' in message
    assert 'OpenAI &lt;Preview&gt; &amp; Launch' in message
    assert 'Official-AI / Model' in message
    assert 'https://example.com/article?x=1&amp;y=2' in message
    assert '\u7c7b\u578b:' in message


def test_build_digest_messages_includes_overview_and_details(monkeypatch) -> None:
    monkeypatch.setattr('app.delivery.emailer.settings.telegram_message_soft_limit', 700)
    rows = []
    for index in range(4):
        row = _item('AI-Industry', 'openai-news', subcategory='Official-AI,Model', title=f'OpenAI release {index}')
        row.summary = 'A' * 400
        row.score = 18 - index
        rows.append(row)

    source = Source(id='openai-news', name='OpenAI News', category='AI-Industry', region='global', type='rss', url='https://example.com', enabled=True, priority=5, tags='ai')
    messages = _build_digest_messages('AI\u60c5\u62a5\u65e5\u62a5', rows, {'openai-news': source}, {}, kind='ai', daily=True)

    assert len(messages) >= 2
    assert '\u4eca\u65e5\u7ed3\u8bba' in messages[0]
    assert '\u8be6\u60c5' in messages[1]


def test_build_digest_messages_groups_same_event(monkeypatch) -> None:
    monkeypatch.setattr('app.delivery.emailer.settings.telegram_message_soft_limit', 3600)
    monkeypatch.setattr('app.delivery.emailer.settings.telegram_enable_event_grouping', True)
    first = _item('AI-Industry', 'openai-news', subcategory='Official-AI,Model', title='OpenAI launches model X')
    second = _item('AI-Industry', 'hackernews-openai', subcategory='Community,Model', title='OpenAI launches model X')
    second.source_id = 'hackernews-openai'
    second.score = 8.2
    source_map = {
        'openai-news': Source(id='openai-news', name='OpenAI News', category='AI-Industry', region='global', type='rss', url='https://example.com', enabled=True, priority=5, tags='ai'),
        'hackernews-openai': Source(id='hackernews-openai', name='Hacker News', category='AI-Industry', region='global', type='rss', url='https://example.com/hn', enabled=True, priority=4, tags='ai'),
    }

    messages = _build_digest_messages('AI\u60c5\u62a5\u65e5\u62a5', [first, second], source_map, {}, kind='ai', daily=True)

    detail = '\n'.join(messages[1:])
    assert '\u540c\u4e8b\u4ef6\u6765\u6e90: 2 \u4e2a' in detail
    assert detail.count('<b>1.') == 1


def test_telegram_delivery_configured_with_separate_chat_ids(monkeypatch) -> None:
    monkeypatch.setattr('app.delivery.emailer.settings.delivery_telegram_enabled', True)
    monkeypatch.setattr('app.delivery.emailer.settings.telegram_bot_token', 'token')
    monkeypatch.setattr('app.delivery.emailer.settings.telegram_chat_id', '')
    monkeypatch.setattr('app.delivery.emailer.settings.telegram_ai_chat_id', '1001')
    monkeypatch.setattr('app.delivery.emailer.settings.telegram_policy_chat_id', '1002')

    assert telegram_delivery_configured()
