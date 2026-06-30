from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Item, TopicSnapshot

POLICY_SOURCE_IDS = {"gov-news", "gov-policy", "miit", "miit-policy", "ndrc-notice", "cac-gov"}
OFFICIAL_POLICY_SOURCE_IDS = {"gov-policy", "miit-policy", "ndrc-notice"}
WECHAT_POLICY_PREFIX = "wechat-policy-"
WECHAT_AI_PREFIX = "wechat-ai-"
AI_PRIMARY_PREFIXES = ("openai-", "anthropic-", "deepmind-", "huggingface-", "paperswithcode-", "arxiv-")
AI_ALL_PREFIXES = AI_PRIMARY_PREFIXES + ("reddit-", "hackernews-", "github-", WECHAT_AI_PREFIX)


def sort_dt(item: Item) -> datetime:
    return item.published_at or item.fetched_at or datetime.min


def item_text(item: Item) -> str:
    return " ".join(filter(None, [item.title or "", item.summary or "", item.raw_content or ""])).lower()


def is_policy(item: Item) -> bool:
    if item.source_id in POLICY_SOURCE_IDS or item.source_id.startswith(WECHAT_POLICY_PREFIX):
        return True
    if item.source_id.startswith(AI_ALL_PREFIXES):
        return False
    return item.category.startswith("Policy-") or item.region == "cn"


def is_ai(item: Item) -> bool:
    return item.category.startswith("AI-") or item.source_id.startswith(AI_ALL_PREFIXES)


def item_track_code(item: Item) -> str:
    if is_policy(item):
        text = item_text(item)
        if item.source_id.endswith("-policy") or item.source_id in OFFICIAL_POLICY_SOURCE_IDS:
            return "official"
        if item.source_id.startswith(WECHAT_POLICY_PREFIX):
            return "supplement"
        if "\u5f81\u6c42\u610f\u89c1" in text or "\u7b54\u8bb0\u8005\u95ee" in text:
            return "broad"
        return "broad"
    if item.source_id.startswith(WECHAT_AI_PREFIX):
        return "supplement"
    if item.source_id.startswith(AI_PRIMARY_PREFIXES):
        return "official"
    return "broad"


def topic_name_for_item(item: Item, kind: str) -> str:
    tags = [tag.strip() for tag in (item.subcategory or "").split(",") if tag.strip()]
    if kind == "ai":
        priority = ["openai", "anthropic", "deepmind", "huggingface", "github", "llm", "OpenSource", "Official-AI", "Community", "research", "model"]
        stopwords = {"ai", "AI", "official", "community", "wechat", "supplement", "media", "news", "product", "startup", "tutorial", "opensource", "OpenSource"}
    else:
        priority = ["Regulation", "Planning", "Subsidy", "Central-Policy", "Local-Policy", "security", "data", "cyberspace", "ndrc", "AI"]
        stopwords = {"policy", "central", "industry", "wechat", "supplement", "broad-news", "official-policy", "test"}
    for key in priority:
        if key in tags:
            return key
    for tag in tags:
        if tag not in stopwords:
            return tag
    return item.category or ("AI" if kind == "ai" else "Policy")


def filter_rows(rows: list[Item], kind: str, days: int) -> list[Item]:
    filtered = [row for row in rows if is_ai(row)] if kind == "ai" else [row for row in rows if is_policy(row)]
    if days > 0:
        cutoff = datetime.utcnow() - timedelta(days=days)
        filtered = [row for row in filtered if sort_dt(row) >= cutoff]
    return sorted(filtered, key=lambda row: (sort_dt(row), row.score or 0.0), reverse=True)


def build_topic_clusters(rows: list[Item], kind: str, days: int, limit: int = 24) -> list[dict[str, Any]]:
    filtered = filter_rows(rows, kind, days)
    clusters: dict[str, dict[str, Any]] = {}
    for row in filtered:
        topic = topic_name_for_item(row, kind)
        cluster = clusters.setdefault(topic, {
            "topic": topic,
            "count": 0,
            "latest_at": None,
            "tracks": Counter(),
            "sources": set(),
            "items": [],
        })
        cluster["count"] += 1
        cluster["tracks"][item_track_code(row)] += 1
        cluster["sources"].add(row.source_id)
        seen_at = sort_dt(row)
        if cluster["latest_at"] is None or seen_at > cluster["latest_at"]:
            cluster["latest_at"] = seen_at
        cluster["items"].append(row)

    ordered = sorted(clusters.values(), key=lambda cluster: (cluster["count"], cluster["latest_at"] or datetime.min), reverse=True)
    cards: list[dict[str, Any]] = []
    for cluster in ordered[:limit]:
        sample_rows = sorted(cluster["items"], key=lambda row: (row.score or 0.0, sort_dt(row)), reverse=True)[:8]
        cards.append({
            "topic": cluster["topic"],
            "count": cluster["count"],
            "latest_at": cluster["latest_at"],
            "official_count": cluster["tracks"].get("official", 0),
            "broad_count": cluster["tracks"].get("broad", 0),
            "supplement_count": cluster["tracks"].get("supplement", 0),
            "source_count": len(cluster["sources"]),
            "sample_rows": sample_rows,
        })
    return cards


def snapshot_date_key(snapshot_at: datetime | None = None) -> str:
    point = snapshot_at or datetime.utcnow()
    return point.strftime("%Y-%m-%d")


def snapshot_topic_clusters(session: Session, windows: tuple[int, ...] = (7, 30, 90, 180, 365), kinds: tuple[str, ...] = ("ai", "policy"), snapshot_at: datetime | None = None) -> dict[str, int]:
    point = snapshot_at or datetime.utcnow()
    date_key = snapshot_date_key(point)
    rows = session.execute(select(Item)).scalars().all()
    session.execute(delete(TopicSnapshot).where(TopicSnapshot.snapshot_date == date_key))
    created = 0
    topic_total = 0
    for kind in kinds:
        for window in windows:
            cards = build_topic_clusters(rows, kind, window, limit=60)
            topic_total += len(cards)
            for card in cards:
                session.add(TopicSnapshot(
                    kind=kind,
                    topic=card["topic"],
                    window_days=window,
                    item_count=card["count"],
                    official_count=card["official_count"],
                    broad_count=card["broad_count"],
                    supplement_count=card["supplement_count"],
                    source_count=card["source_count"],
                    latest_at=card["latest_at"],
                    snapshot_date=date_key,
                    snapshot_at=point,
                ))
                created += 1
    return {"rows": created, "topics": topic_total}


def load_topic_snapshot_deltas(session: Session, kind: str, window_days: int, topics: list[str], current_date: str | None = None) -> dict[str, dict[str, Any]]:
    if not topics:
        return {}
    active_date = current_date or snapshot_date_key()
    rows = session.execute(
        select(TopicSnapshot)
        .where(
            TopicSnapshot.kind == kind,
            TopicSnapshot.window_days == window_days,
            TopicSnapshot.snapshot_date != active_date,
            TopicSnapshot.topic.in_(topics),
        )
        .order_by(TopicSnapshot.snapshot_at.desc())
    ).scalars().all()
    latest: dict[str, TopicSnapshot] = {}
    for row in rows:
        if row.topic not in latest:
            latest[row.topic] = row
    return {
        topic: {
            "previous_count": row.item_count,
            "previous_at": row.snapshot_at,
            "previous_date": row.snapshot_date,
        }
        for topic, row in latest.items()
    }


def load_topic_history_series(session: Session, kind: str, window_days: int, lookback: int = 14, limit: int = 18, sort_by: str = "hot", trend: str = "all") -> dict[str, Any]:
    rows = session.execute(
        select(TopicSnapshot)
        .where(TopicSnapshot.kind == kind, TopicSnapshot.window_days == window_days)
        .order_by(TopicSnapshot.snapshot_date.asc(), TopicSnapshot.snapshot_at.asc())
    ).scalars().all()
    if not rows:
        return {"series": [], "date_labels": [], "series_count": 0, "latest_snapshot_date": "", "window_days": window_days}

    unique_dates = sorted({row.snapshot_date for row in rows})
    dates = unique_dates[-lookback:]
    active_rows = [row for row in rows if row.snapshot_date in dates]
    latest_date = dates[-1]

    latest_rows: dict[str, TopicSnapshot] = {}
    for row in active_rows:
        if row.snapshot_date != latest_date:
            continue
        current = latest_rows.get(row.topic)
        if current is None or row.item_count > current.item_count:
            latest_rows[row.topic] = row

    by_topic_date: dict[tuple[str, str], TopicSnapshot] = {}
    for row in active_rows:
        by_topic_date[(row.topic, row.snapshot_date)] = row

    max_count = max((row.item_count for row in active_rows if row.topic in latest_rows), default=1)
    pre_series: list[dict[str, Any]] = []
    for topic in latest_rows:
        points: list[dict[str, Any]] = []
        latest_count = 0
        previous_count = 0
        peak_count = 0
        latest_official = 0
        latest_broad = 0
        latest_supplement = 0
        latest_sources = 0
        for index, date_value in enumerate(dates):
            row = by_topic_date.get((topic, date_value))
            count = int(row.item_count) if row else 0
            peak_count = max(peak_count, count)
            if date_value == latest_date:
                latest_count = count
                latest_official = int(row.official_count) if row else 0
                latest_broad = int(row.broad_count) if row else 0
                latest_supplement = int(row.supplement_count) if row else 0
                latest_sources = int(row.source_count) if row else 0
            if index == max(len(dates) - 2, 0):
                previous_count = count
            height = 12 if max_count <= 0 else max(12, round((count / max_count) * 100)) if count > 0 else 6
            points.append({
                "date": date_value,
                "count": count,
                "height": height,
                "label": date_value[5:].replace("-", "/"),
                "active": date_value == latest_date,
            })
        delta = latest_count - previous_count if len(dates) > 1 else latest_count
        if trend == "rising" and delta <= 0:
            continue
        if trend == "falling" and delta >= 0:
            continue
        if trend == "stable" and delta != 0:
            continue
        pre_series.append({
            "topic": topic,
            "latest_count": latest_count,
            "previous_count": previous_count,
            "delta": delta,
            "delta_label": f"{delta:+d}",
            "delta_tone": "good" if delta > 0 else "warning" if delta < 0 else "neutral",
            "latest_official_count": latest_official,
            "latest_broad_count": latest_broad,
            "latest_supplement_count": latest_supplement,
            "latest_source_count": latest_sources,
            "peak_count": peak_count,
            "points": points,
        })

    if sort_by == "rising":
        pre_series.sort(key=lambda row: (row["delta"], row["latest_count"], row["latest_official_count"], row["topic"]), reverse=True)
    elif sort_by == "coverage":
        pre_series.sort(key=lambda row: (row["latest_source_count"], row["latest_official_count"], row["latest_count"], row["topic"]), reverse=True)
    else:
        pre_series.sort(key=lambda row: (row["latest_count"], row["latest_official_count"], row["peak_count"], row["topic"]), reverse=True)

    series = pre_series[:limit]
    return {
        "series": series,
        "date_labels": dates,
        "series_count": len(series),
        "latest_snapshot_date": latest_date,
        "window_days": window_days,
    }

