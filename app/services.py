from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.audit.oss_radar import seed_default_oss_projects
from app.classifiers.keywords import KeywordClassifier
from app.collectors.arxiv import ArxivCollector
from app.collectors.github import GitHubCollector
from app.collectors.html_policy import HTMLPolicyCollector
from app.collectors.rss import RSSCollector
from app.config import settings
from app.db.base import SessionLocal
from app.db.init_db import init_db
from app.db.models import Item, RunLog, Source
from app.delivery.emailer import prepare_delivery_items, send_digest_via_email, send_ops_alert, send_split_telegram_digests
from app.digest.generator import render_digest, render_oss_radar
from app.policy_lifecycle import apply_policy_lifecycle, link_superseded_policies, refresh_policy_lifecycle
from app.scoring.engine import ScoreEngine
from app.schemas import CollectedItem, SourceDefinition
from app.source_registry import load_source_definitions
from app.topic_intel import is_policy, snapshot_topic_clusters
from app.utils.dedup import deduplicate_items, items_look_duplicate
from app.utils.normalize import item_hash, normalize_item, normalize_url


DAILY_WARNING_STATUS = 'partial'


def _collector_for(source_type: str):
    mapping = {
        'rss': RSSCollector(),
        'html': HTMLPolicyCollector(),
        'github': GitHubCollector(),
        'arxiv': ArxivCollector(),
    }
    return mapping[source_type]


def sync_sources(session, definitions: list[SourceDefinition]) -> None:
    for definition in definitions:
        existing = session.get(Source, definition.id)
        if existing is None:
            session.add(
                Source(
                    id=definition.id,
                    name=definition.name,
                    category=definition.category,
                    region=definition.region,
                    type=definition.type,
                    url=definition.url,
                    enabled=definition.enabled,
                    priority=definition.priority,
                    tags=','.join(definition.tags),
                )
            )
        else:
            existing.name = definition.name
            existing.category = definition.category
            existing.region = definition.region
            existing.type = definition.type
            existing.url = definition.url
            existing.enabled = definition.enabled
            existing.priority = definition.priority
            existing.tags = ','.join(definition.tags)
    session.commit()


def _collect_all(definitions: list[SourceDefinition]) -> tuple[list[tuple[CollectedItem, SourceDefinition]], list[str]]:
    collected: list[tuple[CollectedItem, SourceDefinition]] = []
    warnings: list[str] = []
    for definition in definitions:
        if not definition.enabled:
            continue
        try:
            collector = _collector_for(definition.type)
            for item in collector.collect(definition):
                collected.append((normalize_item(item), definition))
        except Exception as exc:
            warnings.append(f'{definition.id}: {exc}')
    return collected, warnings


def _run_message(collected_count: int, unique_count: int, inserted_count: int, duplicate_count: int, warnings: list[str], extra_bits: list[str] | None = None) -> str:
    bits = [
        f'collected={collected_count}',
        f'unique={unique_count}',
        f'inserted={inserted_count}',
        f'duplicates={duplicate_count}',
    ]
    if extra_bits:
        bits.extend(extra_bits)
    if warnings:
        bits.extend(warnings)
    return '; '.join(bits)


def _health_alert_lines(session, definitions: list[SourceDefinition], warnings: list[str]) -> list[str]:
    """Only notify on collection failures; an idle source is not a failed source."""
    if not warnings:
        return []
    enabled_count = len([definition for definition in definitions if definition.enabled])
    lines = [
        f"\u68c0\u67e5\u65f6\u95f4: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"\u5df2\u542f\u7528\u4fe1\u606f\u6e90: {enabled_count}",
        f"\u91c7\u96c6\u5f02\u5e38 {len(warnings)} \u9879:",
    ]
    lines.extend([f"\u2022 {warning[:180]}" for warning in warnings[:6]])
    return lines


def _stored_signatures(rows: list[Item]) -> list[dict[str, object]]:
    return [
        {
            'hash': row.hash,
            'url': normalize_url(row.normalized_url or row.url),
            'title': row.title or '',
            'published_at': row.published_at or row.fetched_at,
        }
        for row in rows
    ]


def _looks_like_stored_duplicate(item: CollectedItem, signatures: list[dict[str, object]]) -> bool:
    digest_hash = item_hash(item.title, item.url)
    normalized = normalize_url(item.url)
    for signature in signatures:
        if signature['hash'] == digest_hash or signature['url'] == normalized:
            return True
        if items_look_duplicate(item.title, item.published_at, str(signature['title']), signature['published_at'], normalized, str(signature['url'])):
            return True
    return False


def _append_signature(signatures: list[dict[str, object]], item: CollectedItem | Item) -> None:
    signatures.append({
        'hash': item.hash if isinstance(item, Item) else item_hash(item.title, item.url),
        'url': normalize_url(item.normalized_url if isinstance(item, Item) else item.url),
        'title': item.title,
        'published_at': item.published_at if isinstance(item, Item) else item.published_at,
    })


def _refresh_policy_relations(session) -> None:
    rows = session.execute(select(Item)).scalars().all()
    policies = [row for row in rows if is_policy(row)]
    for row in sorted(policies, key=lambda value: value.published_at or value.fetched_at or datetime.min):
        link_superseded_policies(session, row)
    refresh_policy_lifecycle(session)


def _policy_backlog_counts(session, window_days: int = 30, stale_days: int = 14) -> dict[str, int]:
    rows = session.execute(select(Item)).scalars().all()
    policies = [row for row in rows if is_policy(row)]
    now = datetime.utcnow()
    future = now + timedelta(days=window_days)
    stale_cutoff = now - timedelta(days=stale_days)
    return {
        'policies': len(policies),
        'unknown': len([row for row in policies if (row.status or 'unknown') == 'unknown']),
        'expiring': len([row for row in policies if row.expires_at and now <= row.expires_at <= future and (row.status or 'unknown') not in {'inactive', 'superseded'}]),
        'overdue': len([row for row in policies if row.expires_at and row.expires_at < now and (row.status or 'unknown') != 'inactive']),
        'recheck': len([row for row in policies if row.last_checked_at is None or row.last_checked_at < stale_cutoff]),
    }


def _send_daily_delivery(session, output_path: Path, new_items: list[Item]) -> list[str]:
    if not new_items:
        return ['delivery skipped: no new items']
    return [
        send_digest_via_email(output_path, 'Daily AI and Policy Digest'),
        *send_split_telegram_digests(session, daily=True, items=new_items),
    ]


def _send_weekly_delivery(session, weekly_path: Path, weekly_items: list[Item]) -> list[str]:
    if not weekly_items:
        return ['delivery skipped: no new items in last 7 days']
    return [
        send_digest_via_email(weekly_path, 'Weekly AI and Policy Digest'),
        *send_split_telegram_digests(session, daily=False, items=weekly_items),
    ]


def run_daily() -> Path:
    init_db()
    definitions = load_source_definitions(settings.config_dir / 'sources.yaml')
    classifier = KeywordClassifier(settings.config_dir / 'keywords.yaml')
    scorer = ScoreEngine(settings.config_dir / 'scoring.yaml')

    collected_pairs, warnings = _collect_all(definitions)
    unique_items = deduplicate_items([pair[0] for pair in collected_pairs])
    definition_map = {definition.id: definition for definition in definitions}
    output_path = settings.digest_dir / 'daily_digest.md'
    inserted_count = 0
    stored_duplicate_count = 0

    with SessionLocal() as session:
        sync_sources(session, definitions)
        existing_rows = session.execute(select(Item).order_by(Item.fetched_at.desc()).limit(4000)).scalars().all()
        signatures = _stored_signatures(existing_rows)
        new_item_ids: list[int] = []
        for item in unique_items:
            classification = classifier.classify(item)
            source_definition = definition_map[item.source_id]
            score, reason = scorer.score(item, source_definition, classification.category)
            if _looks_like_stored_duplicate(item, signatures):
                stored_duplicate_count += 1
                continue

            db_item = Item(
                source_id=item.source_id,
                title=item.title,
                url=item.url,
                normalized_url=item.url,
                published_at=item.published_at,
                fetched_at=datetime.utcnow(),
                category=classification.category,
                subcategory=','.join(classification.tags),
                region=item.region,
                summary=item.raw_summary[:500],
                reason=f"{reason}; matched={','.join(classification.matched_keywords)}" if classification.matched_keywords else reason,
                score=score,
                raw_content=item.raw_content[:4000],
                hash=item_hash(item.title, item.url),
            )
            apply_policy_lifecycle(db_item)
            session.add(db_item)
            session.flush()
            link_superseded_policies(session, db_item)
            _append_signature(signatures, db_item)
            new_item_ids.append(db_item.id)
            inserted_count += 1

        _refresh_policy_relations(session)
        snapshot_stats = snapshot_topic_clusters(session)
        session.add(
            RunLog(
                run_type='daily',
                status='success' if not warnings else DAILY_WARNING_STATUS,
                message=_run_message(
                    len(collected_pairs),
                    len(unique_items),
                    inserted_count,
                    max(len(collected_pairs) - len(unique_items), 0) + stored_duplicate_count,
                    warnings,
                    extra_bits=[f'stored_duplicates={stored_duplicate_count}', f'topic_snapshots={snapshot_stats["rows"]}'],
                ),
            )
        )
        session.commit()
        new_items = []
        if new_item_ids:
            new_items = session.execute(select(Item).where(Item.id.in_(new_item_ids)).order_by(Item.score.desc(), Item.fetched_at.desc())).scalars().all()
        delivery_items = prepare_delivery_items(new_items)
        render_digest(session, output_path, 'Daily AI and Policy Digest', limit=20, items=delivery_items)
        daily_delivery = _send_daily_delivery(session, output_path, delivery_items)
        session.add(RunLog(run_type='daily-delivery', status='success', message='; '.join(daily_delivery)))
        alert_notice = send_ops_alert(_health_alert_lines(session, definitions, warnings))
        session.add(RunLog(run_type='daily-ops', status='success', message=alert_notice))
        session.commit()
    return output_path


def run_weekly() -> tuple[Path, Path]:
    init_db()
    weekly_path = settings.digest_dir / 'weekly_digest.md'
    radar_path = settings.digest_dir / 'oss_radar.md'
    weekly_cutoff = datetime.utcnow() - timedelta(days=7)
    with SessionLocal() as session:
        seed_default_oss_projects(session)
        _refresh_policy_relations(session)
        snapshot_stats = snapshot_topic_clusters(session)
        weekly_items = session.execute(select(Item).where(Item.fetched_at >= weekly_cutoff).order_by(Item.score.desc(), Item.fetched_at.desc()).limit(200)).scalars().all()
        delivery_items = prepare_delivery_items(weekly_items)
        render_digest(session, weekly_path, 'Weekly AI and Policy Digest', limit=50, items=delivery_items)
        render_oss_radar(session, radar_path)
        weekly_delivery = _send_weekly_delivery(session, weekly_path, delivery_items)
        session.add(RunLog(run_type='weekly', status='success', message='; '.join(['generated weekly artifacts', f'topic_snapshots={snapshot_stats["rows"]}', *weekly_delivery])))
        session.commit()
    return weekly_path, radar_path


def run_policy_refresh() -> dict[str, int]:
    init_db()
    with SessionLocal() as session:
        refresh_policy_lifecycle(session)
        _refresh_policy_relations(session)
        snapshot_stats = snapshot_topic_clusters(session)
        counts = _policy_backlog_counts(session)
        session.add(
            RunLog(
                run_type='policy-refresh',
                status='success',
                message='; '.join([
                    f'policies={counts["policies"]}',
                    f'unknown={counts["unknown"]}',
                    f'expiring={counts["expiring"]}',
                    f'overdue={counts["overdue"]}',
                    f'recheck={counts["recheck"]}',
                    f'topic_snapshots={snapshot_stats["rows"]}',
                ]),
            )
        )
        session.commit()
    return {**counts, **snapshot_stats}
