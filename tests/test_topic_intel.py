from datetime import datetime
from uuid import uuid4

from app.db.base import SessionLocal
from app.db.models import TopicSnapshot
from app.topic_intel import load_topic_snapshot_deltas


def test_topic_snapshot_delta_lookup_returns_latest_previous_snapshot() -> None:
    topic = f"topic-{uuid4().hex}"
    with SessionLocal() as session:
        session.add(TopicSnapshot(kind='ai', topic=topic, window_days=90, item_count=3, official_count=1, broad_count=2, supplement_count=0, source_count=2, latest_at=datetime(2026, 6, 20), snapshot_date='2026-06-27', snapshot_at=datetime(2026, 6, 27, 1, 0, 0)))
        session.add(TopicSnapshot(kind='ai', topic=topic, window_days=90, item_count=5, official_count=2, broad_count=3, supplement_count=0, source_count=3, latest_at=datetime(2026, 6, 28), snapshot_date='2026-06-28', snapshot_at=datetime(2026, 6, 28, 1, 0, 0)))
        session.commit()

    with SessionLocal() as session:
        deltas = load_topic_snapshot_deltas(session, 'ai', 90, [topic], current_date='2026-06-29')
    assert deltas[topic]['previous_count'] == 5
    assert deltas[topic]['previous_date'] == '2026-06-28'


from app.topic_intel import load_topic_history_series


def test_topic_history_series_returns_latest_topics() -> None:
    topic = f"history-{uuid4().hex}"
    with SessionLocal() as session:
        session.add(TopicSnapshot(kind='policy', topic=topic, window_days=365, item_count=2, official_count=1, broad_count=1, supplement_count=0, source_count=1, latest_at=datetime(2026, 6, 27), snapshot_date='2026-06-27', snapshot_at=datetime(2026, 6, 27, 1, 0, 0)))
        session.add(TopicSnapshot(kind='policy', topic=topic, window_days=365, item_count=4, official_count=2, broad_count=2, supplement_count=0, source_count=2, latest_at=datetime(2026, 6, 28), snapshot_date='2026-06-28', snapshot_at=datetime(2026, 6, 28, 1, 0, 0)))
        session.add(TopicSnapshot(kind='policy', topic=topic, window_days=365, item_count=6, official_count=3, broad_count=3, supplement_count=0, source_count=2, latest_at=datetime(2026, 6, 29), snapshot_date='2026-06-29', snapshot_at=datetime(2026, 6, 29, 1, 0, 0)))
        session.commit()

    with SessionLocal() as session:
        result = load_topic_history_series(session, 'policy', 365, lookback=7, limit=10)
    series = [row for row in result['series'] if row['topic'] == topic]
    assert series
    assert series[0]['latest_count'] == 6



def test_topic_history_series_supports_trend_and_coverage_sort() -> None:
    rising_topic = f"rising-{uuid4().hex}"
    falling_topic = f"falling-{uuid4().hex}"
    with SessionLocal() as session:
        session.add(TopicSnapshot(kind='ai', topic=rising_topic, window_days=90, item_count=2, official_count=1, broad_count=1, supplement_count=0, source_count=2, latest_at=datetime(2026, 6, 27), snapshot_date='2026-06-27', snapshot_at=datetime(2026, 6, 27, 1, 0, 0)))
        session.add(TopicSnapshot(kind='ai', topic=rising_topic, window_days=90, item_count=6, official_count=2, broad_count=3, supplement_count=1, source_count=4, latest_at=datetime(2026, 6, 29), snapshot_date='2026-06-29', snapshot_at=datetime(2026, 6, 29, 1, 0, 0)))
        session.add(TopicSnapshot(kind='ai', topic=falling_topic, window_days=90, item_count=5, official_count=3, broad_count=2, supplement_count=0, source_count=5, latest_at=datetime(2026, 6, 27), snapshot_date='2026-06-27', snapshot_at=datetime(2026, 6, 27, 1, 0, 0)))
        session.add(TopicSnapshot(kind='ai', topic=falling_topic, window_days=90, item_count=1, official_count=1, broad_count=0, supplement_count=0, source_count=1, latest_at=datetime(2026, 6, 29), snapshot_date='2026-06-29', snapshot_at=datetime(2026, 6, 29, 1, 0, 0)))
        session.commit()

    with SessionLocal() as session:
        rising = load_topic_history_series(session, 'ai', 90, lookback=7, limit=10, sort_by='coverage', trend='rising')
    topics = [row['topic'] for row in rising['series']]
    assert rising_topic in topics
    assert falling_topic not in topics
