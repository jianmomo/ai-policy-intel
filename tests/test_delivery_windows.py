from datetime import datetime, timedelta
from pathlib import Path

from app.db.models import Item
from app.digest.generator import render_digest


def _make_item(item_id: int, title: str, fetched_at: datetime, score: float = 20.0) -> Item:
    item = Item(
        id=item_id,
        source_id='test-source',
        title=title,
        url=f'https://example.com/{item_id}',
        normalized_url=f'https://example.com/{item_id}',
        published_at=fetched_at,
        fetched_at=fetched_at,
        category='AI-Industry',
        subcategory='AI,test',
        region='global',
        summary='summary',
        reason='reason',
        score=score,
        raw_content='raw',
        hash=f'hash-{item_id}',
    )
    return item


def test_render_digest_uses_provided_items_only(tmp_path: Path) -> None:
    now = datetime.utcnow()
    new_item = _make_item(1, 'New item', now)
    old_item = _make_item(2, 'Old item', now - timedelta(days=30), score=99.0)
    output = tmp_path / 'digest.md'

    render_digest(None, output, 'Daily AI and Policy Digest', limit=20, items=[new_item])

    content = output.read_text(encoding='utf-8')
    assert 'New item' in content
    assert 'Old item' not in content


def test_render_digest_writes_empty_window_notice(tmp_path: Path) -> None:
    output = tmp_path / 'digest.md'

    render_digest(None, output, 'Daily AI and Policy Digest', limit=20, items=[])

    content = output.read_text(encoding='utf-8')
    assert 'No new items in this window.' in content
    assert '- Items: 0' in content
