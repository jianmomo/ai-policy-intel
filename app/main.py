from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.config import settings
from app.db.base import SessionLocal
from app.db.init_db import init_db
from app.db.models import Item, RunLog, Source
from app.policy_lifecycle import apply_policy_lifecycle, clear_manual_override, has_manual_override, parse_admin_date
from app.services import sync_sources
from app.source_registry import load_source_definitions, update_source_definition
from app.topic_intel import build_topic_clusters, item_track_code as cluster_item_track_code, load_topic_snapshot_deltas, topic_name_for_item as cluster_topic_name_for_item
from app.ui_i18n import get_translator, normalize_lang, translate


init_db()
app = FastAPI(title=settings.app_name)

WEB_ROOT = Path(__file__).resolve().parent / "web"
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

POLICY_SOURCE_IDS = {"gov-news", "gov-policy", "miit", "miit-policy", "ndrc-notice", "cac-gov"}
OFFICIAL_POLICY_SOURCE_IDS = {"gov-policy", "miit-policy", "ndrc-notice"}
BROAD_POLICY_SOURCE_IDS = {"gov-news", "miit", "cac-gov"}
WECHAT_POLICY_PREFIX = "wechat-policy-"
WECHAT_AI_PREFIX = "wechat-ai-"
LOCAL_ADMIN_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
AI_PRIMARY_PREFIXES = (
    "openai-",
    "anthropic-",
    "deepmind-",
    "huggingface-",
    "paperswithcode-",
    "arxiv-",
)
AI_ALL_PREFIXES = AI_PRIMARY_PREFIXES + ("reddit-", "hackernews-", "github-", WECHAT_AI_PREFIX)
OVERRIDE_FILTERS = {"all", "manual", "auto"}
POLICY_STATUSES = {"active", "draft", "inactive", "superseded", "unknown"}


def datetime_fmt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")



def date_fmt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d")



def date_input_fmt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")



def score_fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}"



templates.env.filters["datetime_fmt"] = datetime_fmt
templates.env.filters["date_fmt"] = date_fmt
templates.env.filters["date_input_fmt"] = date_input_fmt
templates.env.filters["score_fmt"] = score_fmt



def _source_tags(source: Source | None) -> list[str]:
    if not source or not source.tags:
        return []
    return [tag.strip() for tag in source.tags.split(",") if tag.strip()]



def _item_tags(item: Item, source: Source | None) -> list[str]:
    tags: list[str] = []
    if item.subcategory:
        tags.extend([tag.strip() for tag in item.subcategory.split(",") if tag.strip()])
    for tag in _source_tags(source):
        if tag not in tags:
            tags.append(tag)
    return tags



def _item_text(item: Item) -> str:
    return " ".join(filter(None, [item.title or "", item.summary or "", item.raw_content or ""])).lower()



def _is_policy(item: Item) -> bool:
    if item.source_id in POLICY_SOURCE_IDS or item.source_id.startswith(WECHAT_POLICY_PREFIX):
        return True
    if item.source_id.startswith(AI_ALL_PREFIXES):
        return False
    return item.category.startswith("Policy-") or item.region == "cn"



def _is_ai(item: Item) -> bool:
    return item.category.startswith("AI-") or item.source_id.startswith(AI_ALL_PREFIXES)


def _policy_signal(item: Item) -> dict[str, str]:
    text = _item_text(item)
    if "\u5f81\u6c42\u610f\u89c1" in text or "\u7b54\u8bb0\u8005\u95ee" in text:
        return {"code": "interpretation", "tone": "info"}
    if item.source_id.endswith("-policy") or item.source_id in OFFICIAL_POLICY_SOURCE_IDS:
        return {"code": "official", "tone": "good"}
    if item.source_id.startswith(WECHAT_POLICY_PREFIX):
        return {"code": "supplement", "tone": "pending"}
    return {"code": "broad", "tone": "warning"}



def _policy_lifecycle_state(item: Item, lang: str) -> dict[str, str]:
    code = (item.status or "unknown").strip() or "unknown"
    tone = {
        "active": "good",
        "draft": "pending",
        "inactive": "warning",
        "superseded": "info",
        "unknown": "neutral",
    }.get(code, "neutral")
    return {"code": code, "tone": tone, "label": translate(lang, f"policy.status.{code}")}



def _summary_text(item: Item) -> str:
    text = (item.summary or item.raw_content or "").strip()
    text = " ".join(text.split())
    return text[:220]



def _build_path(path: str, *, lang: str, admin_token: str = "", **extra: Any) -> str:
    params = {"lang": lang}
    if admin_token:
        params["admin_token"] = admin_token
    for key, value in extra.items():
        if value is None or value == "":
            continue
        params[key] = value
    return f"{path}?{urlencode(params)}"



def _load_sources(session) -> dict[str, Source]:
    rows = session.execute(select(Source)).scalars().all()
    return {row.id: row for row in rows}



def _sort_dt(item: Item) -> datetime:
    return item.published_at or item.fetched_at or datetime.min



def _load_recent_items(session, limit: int = 600) -> list[Item]:
    return session.execute(select(Item).order_by(Item.fetched_at.desc(), Item.score.desc()).limit(limit)).scalars().all()



def _dashboard_context(lang: str, admin_token: str = "") -> dict[str, object]:
    with SessionLocal() as session:
        source_count = session.scalar(select(func.count()).select_from(Source)) or 0
        item_count = session.scalar(select(func.count()).select_from(Item)) or 0
        run_count = session.scalar(select(func.count()).select_from(RunLog)) or 0
        source_map = _load_sources(session)
        rows = _load_recent_items(session)

    ai_rows = sorted([row for row in rows if _is_ai(row)], key=lambda row: (row.score or 0.0, _sort_dt(row)), reverse=True)
    policy_rows = sorted([row for row in rows if _is_policy(row)], key=lambda row: (row.score or 0.0, _sort_dt(row)), reverse=True)
    recent_rows = sorted(rows, key=lambda row: row.fetched_at or datetime.min, reverse=True)[:8]
    latest_fetch = max((row.fetched_at for row in rows if row.fetched_at), default=None)
    active_policy_count = len([row for row in policy_rows if (row.status or "unknown") == "active"])
    source_overview = _source_overview_rows(lang, admin_token, window=7, preview_limit=6)

    return {
        "stats": {
            "sources": int(source_count),
            "total_items": int(item_count),
            "runs": int(run_count),
            "policy_items": len(policy_rows),
            "ai_items": len(ai_rows),
            "active_policies": active_policy_count,
        },
        "hero": {"latest_fetch": latest_fetch, "wechat_supplements": len([row for row in rows if row.source_id.startswith("wechat-")])},
        "policy_focus": [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in policy_rows[:6]],
        "ai_focus": [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in ai_rows[:6]],
        "recent_feed": [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in recent_rows],
        "source_health_preview": source_overview["source_preview"],
        "collector_warnings": source_overview["collector_warnings"][:3],
        "source_health_summary": source_overview["source_summary"],
    }


def _source_track(source: Source) -> str:
    tags = set(_source_tags(source))
    if source.category == "policy":
        if source.id.startswith(WECHAT_POLICY_PREFIX):
            return "supplement"
        if source.id in OFFICIAL_POLICY_SOURCE_IDS or "official-policy" in tags:
            return "official"
        if source.id in BROAD_POLICY_SOURCE_IDS or "broad-news" in tags:
            return "broad"
        return "official" if source.priority >= 9 else "broad"

    if source.id.startswith(WECHAT_AI_PREFIX):
        return "supplement"
    if source.id.startswith(AI_PRIMARY_PREFIXES) or source.type in {"arxiv"} or "official" in tags or "research" in tags:
        return "official"
    return "broad"



def _source_warning_map(runs: list[RunLog], source_ids: list[str]) -> dict[str, dict[str, object]]:
    warnings: dict[str, dict[str, object]] = {}
    for run in runs:
        message = (run.message or "").strip()
        if not message:
            continue
        for source_id in source_ids:
            if source_id in warnings:
                continue
            marker = f"{source_id}:"
            position = message.find(marker)
            if position < 0:
                continue
            excerpt = message[position: position + 220].splitlines()[0].strip()
            warnings[source_id] = {"message": excerpt, "created_at": run.created_at}
    return warnings



def _source_overview_rows(lang: str, admin_token: str, window: int = 7, preview_limit: int = 6) -> dict[str, object]:
    ui = _sources_ui(lang)
    with SessionLocal() as session:
        sources = session.execute(select(Source).order_by(Source.category.asc(), Source.priority.desc(), Source.name.asc())).scalars().all()
        items = session.execute(select(Item)).scalars().all()
        runs = session.execute(select(RunLog).order_by(RunLog.created_at.desc()).limit(20)).scalars().all()

    by_source: dict[str, list[Item]] = {}
    for item in items:
        by_source.setdefault(item.source_id, []).append(item)

    warning_map = _source_warning_map(runs, [source.id for source in sources])
    cutoff = datetime.utcnow() - timedelta(days=window)
    now = datetime.utcnow()
    health_tones = {"healthy": "good", "warning": "warning", "stale": "pending", "empty": "neutral", "disabled": "neutral"}
    track_tones = {"official": "good", "broad": "warning", "supplement": "info"}
    health_order = {"warning": 0, "stale": 1, "empty": 2, "healthy": 3, "disabled": 4}
    rows: list[dict[str, object]] = []

    for source in sources:
        source_items = by_source.get(source.id, [])
        last_seen = max(((row.fetched_at or row.published_at or datetime.min) for row in source_items), default=None)
        recent_count = len([row for row in source_items if (row.fetched_at or row.published_at or datetime.min) >= cutoff])
        total_count = len(source_items)
        track_code = _source_track(source)
        warning = warning_map.get(source.id)

        if not source.enabled:
            health_code = "disabled"
        elif warning is not None:
            health_code = "warning"
        elif recent_count > 0:
            health_code = "healthy"
        elif total_count > 0:
            health_code = "stale"
        else:
            health_code = "empty"

        quality_score = 0
        if source.enabled:
            quality_score = 38 + min(recent_count * 8, 32) + min(total_count, 10)
            if last_seen and last_seen >= now - timedelta(days=1):
                quality_score += 12
            elif last_seen and last_seen >= now - timedelta(days=3):
                quality_score += 6
            if track_code == "official":
                quality_score += 6
            quality_score += min(source.priority, 12)
            if health_code == "warning":
                quality_score -= 34
            elif health_code == "stale":
                quality_score -= 18
            elif health_code == "empty":
                quality_score -= 26
        quality_score = max(0, min(int(quality_score), 100))
        quality_code = "high" if quality_score >= 75 else "medium" if quality_score >= 45 else "low"

        rows.append({
            "id": source.id,
            "name": source.name,
            "category": source.category,
            "region": source.region,
            "type": source.type,
            "url": source.url,
            "enabled": bool(source.enabled),
            "priority": source.priority,
            "tags": _source_tags(source),
            "last_seen_at": last_seen,
            "recent_count": recent_count,
            "total_count": total_count,
            "warning_message": warning["message"] if warning else "",
            "warning_at": warning["created_at"] if warning else None,
            "library_path": _build_path("/ui/policies" if source.category == "policy" else "/ui/ai", lang=lang, admin_token=admin_token, source_id=source.id),
            "track": {"code": track_code, "label": ui[f"track_{track_code}"], "tone": track_tones[track_code]},
            "health": {"code": health_code, "label": ui[f"health_{health_code}"], "tone": health_tones[health_code]},
            "quality": {"score": quality_score, "code": quality_code, "label": ui[f"quality_{quality_code}"], "tone": "good" if quality_code == "high" else "pending" if quality_code == "medium" else "warning"},
        })

    rows.sort(key=lambda row: (health_order[row["health"]["code"]], -int(row["recent_count"]), -int(row["priority"]), -(int(row["last_seen_at"].timestamp()) if row["last_seen_at"] else 0), row["name"]))
    warnings = [{"source_id": row["id"], "source_name": row["name"], "message": row["warning_message"], "created_at": row["warning_at"]} for row in rows if row["warning_message"]]
    warnings.sort(key=lambda row: row["created_at"] or datetime.min, reverse=True)

    return {
        "source_preview": rows[:preview_limit],
        "policy_sources": [row for row in rows if row["category"] == "policy"],
        "ai_sources": [row for row in rows if row["category"] == "ai"],
        "collector_warnings": warnings,
        "source_summary": {
            "total": len(rows),
            "enabled": len([row for row in rows if row["enabled"]]),
            "warning": len([row for row in rows if row["health"]["code"] == "warning"]),
            "stale": len([row for row in rows if row["health"]["code"] == "stale"]),
            "empty": len([row for row in rows if row["health"]["code"] == "empty"]),
            "policy": len([row for row in rows if row["category"] == "policy"]),
            "ai": len([row for row in rows if row["category"] == "ai"]),
        },
        "window": window,
    }



def _normalized_title(value: str) -> str:
    text = re.sub(r"[\W_]+", " ", (value or "").lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _title_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for token in _normalized_title(value).split():
        if len(token) >= 3 or any(ord(ch) > 127 for ch in token):
            tokens.append(token)
    return tokens


def _policy_relation_score(reference: Item, candidate: Item, *, match_text: str) -> int:
    ref_norm = _normalized_title(match_text)
    cand_norm = _normalized_title(candidate.title or "")
    if not ref_norm or not cand_norm:
        return 0
    if ref_norm == cand_norm:
        score = 100
    elif ref_norm in cand_norm or cand_norm in ref_norm:
        score = 82
    else:
        overlap = set(_title_tokens(match_text)) & set(_title_tokens(candidate.title or ""))
        score = len(overlap) * 12
    ref_dt = _sort_dt(reference)
    cand_dt = _sort_dt(candidate)
    if cand_dt >= ref_dt:
        score += 6
    else:
        score -= 4
    if candidate.source_id.endswith("-policy") or candidate.source_id in OFFICIAL_POLICY_SOURCE_IDS:
        score += 4
    return score


def _match_policy_successor_candidates(candidates: list[Item], item: Item, limit: int = 3) -> list[Item]:
    target = (item.replaced_by or "").strip()
    if not target:
        return []
    scored: list[tuple[int, Item]] = []
    for row in candidates:
        if row.id == item.id:
            continue
        score = _policy_relation_score(item, row, match_text=target)
        if score >= 24:
            scored.append((score, row))
    scored.sort(key=lambda pair: (pair[0], _sort_dt(pair[1]), pair[1].score or 0.0), reverse=True)
    return [row for _, row in scored[:limit]]


def _match_policy_successor(candidates: list[Item], item: Item) -> Item | None:
    matches = _match_policy_successor_candidates(candidates, item, limit=1)
    return matches[0] if matches else None


def _match_policy_predecessors(candidates: list[Item], item: Item, limit: int = 8) -> list[Item]:
    title = (item.title or "").strip()
    if not title:
        return []
    scored: list[tuple[int, Item]] = []
    for row in candidates:
        if row.id == item.id:
            continue
        match_text = (row.replaced_by or "").strip()
        if not match_text:
            continue
        score = _policy_relation_score(item, row, match_text=match_text)
        if score >= 24:
            scored.append((score, row))
    scored.sort(key=lambda pair: (pair[0], _sort_dt(pair[1]), pair[1].score or 0.0), reverse=True)
    return [row for _, row in scored[:limit]]


def _same_event_key(item: Item) -> str:
    norm = _normalized_title(item.title or "")
    if not norm:
        return f"item-{item.id}"
    tokens = norm.split()
    return " ".join(tokens[:10]) if tokens else norm[:80]


def _group_same_event_cards(cards: list[dict[str, object]], limit: int = 6) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for card in cards:
        groups.setdefault(str(card.get("event_key") or f"item-{card.get('id')}"), []).append(card)
    bundles: list[dict[str, object]] = []
    for grouped in groups.values():
        ordered = sorted(
            grouped,
            key=lambda row: (row.get("score") or 0.0, row.get("published_at") or row.get("fetched_at") or datetime.min),
            reverse=True,
        )
        primary = ordered[0]
        bundles.append(
            {
                "primary": primary,
                "related": ordered[1:4],
                "related_count": max(len(ordered) - 1, 0),
                "source_count": len({row.get("source_id") for row in ordered if row.get("source_id")}),
                "sources": list(dict.fromkeys(str(row.get("source_name") or "") for row in ordered if row.get("source_name")))[:4],
            }
        )
    bundles.sort(
        key=lambda row: (row["primary"].get("score") or 0.0, row["primary"].get("published_at") or row["primary"].get("fetched_at") or datetime.min),
        reverse=True,
    )
    return bundles[:limit]


def _policy_detail_context(session, item: Item, source_map: dict[str, Source], lang: str, admin_token: str) -> dict[str, object]:
    if not _is_policy(item):
        return {"policy_timeline": [], "policy_relations": {"predecessors": [], "successor": None, "candidates": []}}

    ui = _detail_ui(lang)
    rows = session.execute(select(Item)).scalars().all()
    policies = [row for row in rows if _is_policy(row)]
    predecessors = _match_policy_predecessors(policies, item)
    successor = _match_policy_successor(policies, item)
    successor_candidates = _match_policy_successor_candidates(policies, item, limit=3)
    title_set = {(row.title or "").strip() for row in policies if (row.title or "").strip()}
    timeline: list[dict[str, object]] = []

    def push_event(at: datetime | None, label: str, title: str, note: str, tone: str, badge: str = "") -> None:
        timeline.append({"at": at, "label": label, "title": title, "note": note, "tone": tone, "badge": badge})

    source_name = source_map.get(item.source_id).name if source_map.get(item.source_id) else item.source_id
    push_event(item.published_at or item.fetched_at, ui["event_published"], item.title, source_name, "good")
    if item.effective_at:
        push_event(item.effective_at, ui["event_effective"], item.title, item.status_reason or ui["event_note_effective"], "good")
    if item.override_enabled and item.override_updated_at:
        push_event(item.override_updated_at, ui["event_override"], item.title, item.override_reason or ui["event_note_override"], "info", ui["manual_badge"])
    if item.expires_at:
        push_event(item.expires_at, ui["event_expires"], item.title, item.status_reason or ui["event_note_expires"], "warning")
    if successor is not None:
        push_event(successor.effective_at or successor.published_at or successor.fetched_at, ui["event_replaced"], successor.title, successor.status_reason or ui["event_note_replaced"], "info")
    if item.last_checked_at:
        push_event(item.last_checked_at, ui["event_checked"], item.title, item.status_reason or ui["event_note_checked"], "neutral")

    timeline.sort(key=lambda row: ((row["at"] is None), row["at"] or datetime.max))
    return {
        "policy_timeline": timeline,
        "policy_relations": {
            "predecessors": [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in predecessors],
            "successor": _build_card(successor, source_map.get(successor.source_id), lang, admin_token) if successor else None,
            "candidates": [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in successor_candidates if successor is None or row.id != successor.id],
            "audit": _policy_audit_summary(item, lang, successor_exists=bool((item.replaced_by or "").strip() and (item.replaced_by or "").strip() in title_set)),
        },
    }


def _item_track_code(item: Item) -> str:
    return cluster_item_track_code(item)


def _topic_name_for_item(item: Item, kind: str) -> str:
    return cluster_topic_name_for_item(item, kind)


def _topic_cluster_rows(kind: str, lang: str, admin_token: str, days: int, track: str = "all", sort: str = "hot") -> dict[str, object]:
    with SessionLocal() as session:
        source_map = _load_sources(session)
        rows = _load_recent_items(session, limit=4000)
        filtered_rows = [row for row in rows if _matches_track(row, kind, track)]
        clusters = build_topic_clusters(filtered_rows, kind, days, limit=32)
        history = load_topic_snapshot_deltas(session, kind, days, [cluster["topic"] for cluster in clusters])

    cards: list[dict[str, object]] = []
    for cluster in clusters:
        previous = history.get(cluster["topic"])
        if previous is None:
            change = int(cluster["count"])
            delta = {"label": ("新主题" if lang == "zh" else "NEW"), "tone": "info", "value": change}
        else:
            change = int(cluster["count"]) - int(previous["previous_count"])
            delta = {
                "label": f"{change:+d}",
                "tone": "good" if change > 0 else "warning" if change < 0 else "neutral",
                "previous_date": previous["previous_date"],
                "value": change,
            }
        sample_cards = [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in cluster["sample_rows"]]
        cards.append(
            {
                "topic": cluster["topic"],
                "count": cluster["count"],
                "latest_at": cluster["latest_at"],
                "official_count": cluster["official_count"],
                "broad_count": cluster["broad_count"],
                "supplement_count": cluster["supplement_count"],
                "source_count": cluster["source_count"],
                "sample_items": sample_cards,
                "sample_groups": _group_same_event_cards(sample_cards, limit=4),
                "delta": delta,
            }
        )

    if sort == "rising":
        cards.sort(key=lambda row: (row["delta"]["value"] if row["delta"] else 0, row["count"], row["latest_at"] or datetime.min), reverse=True)
    elif sort == "coverage":
        cards.sort(key=lambda row: (row["source_count"], row["official_count"], row["count"], row["latest_at"] or datetime.min), reverse=True)
    else:
        cards.sort(key=lambda row: (row["count"], row["official_count"], row["latest_at"] or datetime.min), reverse=True)
    return {"topics": cards, "topic_count": len(cards), "days": days, "track": track, "sort": sort}



def _gap_hint_rows(lang: str, admin_token: str, window: int = 14) -> dict[str, object]:
    watchlist = [
        {"kind": "ai", "name": "OpenAI / ChatGPT", "keywords": ["openai", "chatgpt", "gpt", "o3", "gpt-4o"]},
        {"kind": "ai", "name": "Anthropic / Claude", "keywords": ["anthropic", "claude"]},
        {"kind": "ai", "name": "Gemini / DeepMind", "keywords": ["gemini", "deepmind", "google ai"]},
        {"kind": "ai", "name": "Open Source LLM", "keywords": ["open source", "opensource", "github", "localllama", "hugging face", "??"]},
        {"kind": "policy", "name": "中国 AI 监管", "keywords": ["中国", "网信", "生成式", "ai", "algorithm", "备案"]},
        {"kind": "policy", "name": "数据安全与治理", "keywords": ["数据安全", "隐私", "合规", "个人信息", "安全"]},
        {"kind": "policy", "name": "算力 / 补贴", "keywords": ["算力", "补贴", "基金", "项目", "subsidy"]},
    ]
    with SessionLocal() as session:
        source_map = _load_sources(session)
        rows = _load_recent_items(session, limit=2400)
    cutoff = datetime.utcnow() - timedelta(days=window)
    recent = [row for row in rows if _sort_dt(row) >= cutoff]
    hints: dict[str, list[dict[str, object]]] = {"ai": [], "policy": []}
    for entry in watchlist:
        matched = []
        for row in recent:
            if entry["kind"] == "ai" and not _is_ai(row):
                continue
            if entry["kind"] == "policy" and not _is_policy(row):
                continue
            haystack = _item_text(row)
            if any(keyword.lower() in haystack for keyword in entry["keywords"]):
                matched.append(row)
        if not matched:
            continue
        track_counts = Counter(_item_track_code(row) for row in matched)
        official_count = track_counts.get("official", 0)
        supplement_count = track_counts.get("supplement", 0)
        broad_count = track_counts.get("broad", 0)
        if official_count > 0 and supplement_count <= official_count and broad_count <= official_count:
            continue
        severity = "warning" if official_count == 0 else "pending"
        reason = "official_gap" if official_count == 0 else "signal_skew"
        hints[entry["kind"]].append({
            "name": entry["name"],
            "official_count": official_count,
            "broad_count": broad_count,
            "supplement_count": supplement_count,
            "tone": severity,
            "reason": reason,
            "sample_items": [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in sorted(matched, key=lambda row: (row.score or 0.0, _sort_dt(row)), reverse=True)[:2]],
        })
    return {"ai_gap_hints": hints["ai"], "policy_gap_hints": hints["policy"], "gap_window": window}



def _matches_track(item: Item, kind: str, track: str) -> bool:
    if track == "all":
        return True
    if kind == "policy":
        if track == "official":
            return item.source_id.endswith("-policy") or item.source_id in OFFICIAL_POLICY_SOURCE_IDS
        if track == "supplement":
            return item.source_id.startswith(WECHAT_POLICY_PREFIX)
        if track == "broad":
            return item.source_id in BROAD_POLICY_SOURCE_IDS or (
                _is_policy(item)
                and not item.source_id.startswith(WECHAT_POLICY_PREFIX)
                and not item.source_id.endswith("-policy")
                and item.source_id not in OFFICIAL_POLICY_SOURCE_IDS
            )
        return True
    if track == "official":
        return item.source_id.startswith(AI_PRIMARY_PREFIXES)
    if track == "supplement":
        return item.source_id.startswith(WECHAT_AI_PREFIX)
    if track == "broad":
        return _is_ai(item) and not item.source_id.startswith(AI_PRIMARY_PREFIXES) and not item.source_id.startswith(WECHAT_AI_PREFIX)
    return True



def _filter_rows(kind: str, rows: list[Item], q: str, track: str, days: int, status: str) -> list[Item]:
    filtered = rows
    if kind == "policy":
        filtered = [row for row in filtered if _is_policy(row)]
    elif kind == "ai":
        filtered = [row for row in filtered if _is_ai(row)]

    if days > 0:
        cutoff = datetime.utcnow() - timedelta(days=days)
        filtered = [row for row in filtered if _sort_dt(row) >= cutoff]

    if q:
        needle = q.lower().strip()
        filtered = [row for row in filtered if needle in _item_text(row)]

    filtered = [row for row in filtered if _matches_track(row, kind, track)]

    if kind == "policy" and status != "all":
        filtered = [row for row in filtered if (row.status or "unknown") == status]

    return sorted(filtered, key=lambda row: (_sort_dt(row), row.score or 0.0), reverse=True)



def _library_rows(
    kind: str,
    q: str,
    source_id: str,
    category: str,
    track: str,
    days: int,
    status: str,
    lang: str,
    admin_token: str = "",
) -> dict[str, object]:
    with SessionLocal() as session:
        source_map = _load_sources(session)
        rows = _load_recent_items(session, limit=1800)

    base_rows = _filter_rows(kind, rows, q, track, days, status)
    source_options_rows = base_rows

    if source_id:
        base_rows = [row for row in base_rows if row.source_id == source_id]
    if category:
        base_rows = [row for row in base_rows if row.category == category]

    cards = [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in base_rows[:120]]
    available_sources = sorted(
        {row.source_id: source_map.get(row.source_id).name if source_map.get(row.source_id) else row.source_id for row in source_options_rows}.items(),
        key=lambda pair: pair[1],
    )
    available_categories = sorted({row.category for row in source_options_rows if row.category})
    status_counts = Counter((row.status or "unknown") for row in source_options_rows if kind == "policy")

    return {
        "items": cards,
        "source_options": available_sources,
        "category_options": available_categories,
        "count": len(base_rows),
        "status_counts": status_counts,
    }



def _admin_access_allowed(request: Request, admin_token: str) -> bool:
    request_host = request.client.host if request.client else ""
    if request_host in LOCAL_ADMIN_HOSTS:
        return True
    if settings.ui_admin_token and admin_token == settings.ui_admin_token:
        return True
    return False



def _require_admin_access(request: Request, admin_token: str) -> None:
    if _admin_access_allowed(request, admin_token):
        return
    raise HTTPException(status_code=403, detail="Admin access requires local access or a valid admin token.")



def _admin_rows(q: str, source_id: str, status: str, override_mode: str, days: int, lang: str, admin_token: str) -> dict[str, object]:
    with SessionLocal() as session:
        source_map = _load_sources(session)
        rows = _load_recent_items(session, limit=1800)

    filtered = [row for row in rows if _is_policy(row)]
    if days > 0:
        cutoff = datetime.utcnow() - timedelta(days=days)
        filtered = [row for row in filtered if _sort_dt(row) >= cutoff]
    if q:
        needle = q.lower().strip()
        filtered = [row for row in filtered if needle in _item_text(row)]
    if source_id:
        filtered = [row for row in filtered if row.source_id == source_id]
    if status != "all":
        filtered = [row for row in filtered if (row.status or "unknown") == status]
    if override_mode == "manual":
        filtered = [row for row in filtered if row.override_enabled]
    elif override_mode == "auto":
        filtered = [row for row in filtered if not row.override_enabled]

    filtered = sorted(filtered, key=lambda row: (_sort_dt(row), row.score or 0.0), reverse=True)
    cards = [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in filtered[:120]]
    source_options = sorted(
        {row.source_id: source_map.get(row.source_id).name if source_map.get(row.source_id) else row.source_id for row in filtered}.items(),
        key=lambda pair: pair[1],
    )
    return {"items": cards, "count": len(filtered), "source_options": source_options}



def _relation_rows(lang: str, admin_token: str) -> dict[str, object]:
    with SessionLocal() as session:
        source_map = _load_sources(session)
        rows = _load_recent_items(session, limit=1800)

    policies = [row for row in rows if _is_policy(row)]
    title_map: dict[str, list[Item]] = {}
    for row in policies:
        title_map.setdefault((row.title or "").strip(), []).append(row)

    groups: dict[str, list[Item]] = {}
    orphan_rows: list[Item] = []
    for row in policies:
        successor_title = (row.replaced_by or "").strip()
        if successor_title:
            groups.setdefault(successor_title, []).append(row)
        elif (row.status or "unknown") == "superseded":
            orphan_rows.append(row)

    group_cards: list[dict[str, object]] = []
    for successor_title, children in groups.items():
        children_sorted = sorted(children, key=lambda row: (_sort_dt(row), row.score or 0.0), reverse=True)
        successor_candidates = sorted(title_map.get(successor_title, []), key=lambda row: (_sort_dt(row), row.score or 0.0), reverse=True)
        successor_card = _build_card(successor_candidates[0], source_map.get(successor_candidates[0].source_id), lang, admin_token) if successor_candidates else None
        child_cards = [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in children_sorted[:20]]
        group_cards.append(
            {
                "successor_title": successor_title,
                "successor": successor_card,
                "children": child_cards,
                "count": len(children_sorted),
            }
        )

    group_cards.sort(key=lambda group: (group["count"], group["successor_title"]), reverse=True)
    orphan_cards = [_build_card(row, source_map.get(row.source_id), lang, admin_token) for row in sorted(orphan_rows, key=lambda row: (_sort_dt(row), row.score or 0.0), reverse=True)[:40]]

    return {
        "groups": group_cards[:80],
        "orphans": orphan_cards,
        "group_count": len(group_cards),
        "orphan_count": len(orphan_rows),
    }



def _queue_ui(lang: str) -> dict[str, str]:
    if lang == "zh":
        return {
            "title": "待处理队列 | AI Policy Intel",
            "eyebrow": "待处理队列",
            "heading": "把待核验、需要复核、即将到期和已过期但状态异常的政策拎出来。",
            "note": "适合每天快速巡检一遍生命周期数据。",
            "nav_label": "待处理队列",
            "back_admin": "返回校正台",
            "window_label": "即将到期窗口",
            "window_days": "天",
            "unknown_label": "待核验政策",
            "recheck_label": "需要复核",
            "expiring_label": "即将到期",
            "overdue_label": "已过期但状态未切换",
            "empty_unknown": "暂无待核验条目。",
            "empty_recheck": "暂无需要复核的条目。",
            "empty_expiring": "暂无即将到期条目。",
            "empty_overdue": "暂无过期状态异常条目。",
            "open_override": "去校正",
        }
    return {
        "title": "Policy Queue | AI Policy Intel",
        "eyebrow": "Policy Queue",
        "heading": "Surface unknown, stale-check, near-expiry, and overdue-but-not-updated policies for daily review.",
        "note": "Use this page as a fast daily lifecycle inspection pass.",
        "nav_label": "Policy queue",
        "back_admin": "Back to override panel",
        "window_label": "Expiry window",
        "window_days": "days",
        "unknown_label": "Needs review",
        "recheck_label": "Needs recheck",
        "expiring_label": "Expiring soon",
        "overdue_label": "Expired but status not switched",
        "empty_unknown": "No unknown policies right now.",
        "empty_recheck": "No stale lifecycle checks right now.",
        "empty_expiring": "No policies are nearing expiry right now.",
        "empty_overdue": "No overdue lifecycle mismatches right now.",
        "open_override": "Open override",
    }


def _relation_ui(lang: str) -> dict[str, str]:
    if lang == "zh":
        return {
            "title": "政策关系视图 | AI Policy Intel",
            "eyebrow": "政策关系视图",
            "heading": "把“被谁替代”这件事按组织出来，方便你顺着政策演化看。",
            "note": "这个版本先做实用视图，后面可以再升级成图谱。",
            "nav_label": "关系视图",
            "back_admin": "返回校正台",
            "group_label": "关联组",
            "successor_label": "新政策",
            "children_label": "被其替代的政策",
            "no_successor": "未在当前库里匹配到新政策正文。",
            "orphan_label": "孤立 superseded 条目",
            "empty_groups": "暂无替代关系数据。",
            "empty_orphans": "暂无孤立 superseded 条目。",
            "open_override": "去校正",
        }
    return {
        "title": "Policy Relations | AI Policy Intel",
        "eyebrow": "Policy Relations",
        "heading": "Group replacement chains so you can follow how policies evolve over time.",
        "note": "This first version favors clarity and daily usability over a heavy graph widget.",
        "nav_label": "Relations",
        "back_admin": "Back to override panel",
        "group_label": "Relation groups",
        "successor_label": "Successor policy",
        "children_label": "Superseded policies",
        "no_successor": "No successor document matched the current stored policy titles.",
        "orphan_label": "Orphan superseded items",
        "empty_groups": "No replacement relationships detected yet.",
        "empty_orphans": "No orphan superseded items right now.",
        "open_override": "Open override",
    }




def _policy_ui(lang: str) -> dict[str, str]:
    if lang == "zh":
        return {
            "admin_label": "生命周期人工校正",
            "sources_label": "来源覆盖页",
            "manual_badge": "人工覆盖",
        }
    return {
        "admin_label": "Lifecycle Overrides",
        "sources_label": "Source Coverage",
        "manual_badge": "Manual override",
    }



def _detail_ui(lang: str) -> dict[str, str]:
    if lang == "zh":
        return {
            "manual_badge": "人工覆盖",
            "open_override": "打开生命周期人工校正",
            "timeline_eyebrow": "生命周期时间线",
            "timeline_title": "政策状态演进",
            "timeline_note": "把发布时间、生效、失效、替代和人工校正放到一条线上，回看时更容易判断这条政策现在是否仍然值得参考。",
            "unknown_time": "时间未知",
            "relation_eyebrow": "替代关系",
            "relation_title": "政策关系链",
            "relation_note": "如果有旧政策被它替代，或者它又被更新政策覆盖，这里会直接串起来。",
            "predecessors_label": "被当前政策替代的旧政策",
            "successor_label": "替代当前政策的新政策",
            "no_predecessors": "还没有识别到被当前政策替代的旧政策。",
            "no_successor": "还没有识别到明确替代当前政策的新政策。",
            "event_published": "发布 / 入库",
            "event_effective": "进入生效区间",
            "event_override": "人工校正更新",
            "event_expires": "到期 / 失效节点",
            "event_replaced": "被后续政策覆盖",
            "event_checked": "最近一次校验",
            "event_note_effective": "依据当前生命周期字段判定进入生效状态。",
            "event_note_override": "运营人工覆盖更新了生命周期结果。",
            "event_note_expires": "根据到期字段或正文信号识别到失效节点。",
            "event_note_replaced": "系统匹配到了替代当前政策的新文件。",
            "event_note_checked": "系统最近一次刷新这条政策生命周期。",
        }
    return {
        "manual_badge": "Manual override",
        "open_override": "Open lifecycle override panel",
        "timeline_eyebrow": "Lifecycle Timeline",
        "timeline_title": "Policy timeline",
        "timeline_note": "This view lines up publication, effective period, expiry, replacement, and manual corrections so you can judge whether a policy is still actionable.",
        "unknown_time": "Unknown time",
        "relation_eyebrow": "Replacement Chain",
        "relation_title": "Policy relations",
        "relation_note": "If this policy superseded earlier guidance or was itself replaced later, the chain appears here.",
        "predecessors_label": "Older policies replaced by this one",
        "successor_label": "Newer policy replacing this one",
        "no_predecessors": "No predecessor policies have been matched yet.",
        "no_successor": "No clear successor policy has been matched yet.",
        "event_published": "Published / stored",
        "event_effective": "Entered effective period",
        "event_override": "Manual override updated",
        "event_expires": "Expiry / inactive point",
        "event_replaced": "Superseded by later policy",
        "event_checked": "Last lifecycle check",
        "event_note_effective": "Lifecycle fields mark this item as effective from this point.",
        "event_note_override": "An operator updated the lifecycle manually.",
        "event_note_expires": "Expiry fields or text signals indicate an inactive point.",
        "event_note_replaced": "A later policy was matched as the successor.",
        "event_note_checked": "Latest automatic lifecycle refresh for this item.",
    }



def _sources_ui(lang: str) -> dict[str, str]:
    if lang == "zh":
        return {
            "nav_label": "来源覆盖页",
            "title": "来源覆盖页 | AI Policy Intel",
            "eyebrow": "来源覆盖",
            "heading": "把信息源是否活着、是否静默、是否报错直接放到页面上。",
            "note": "这页不是给用户看新闻，而是给你判断采集层有没有漏源、死源、重复依赖。",
            "preview_title": "重点关注的来源",
            "preview_eyebrow": "覆盖预览",
            "preview_note": "优先把报错、长期静默或还没有数据的源顶上来。",
            "warning_title": "最近采集告警",
            "warning_eyebrow": "采集告警",
            "warning_empty": "最近没有采集告警。",
            "open_full": "打开完整来源页",
            "back_home": "回到首页",
            "coverage_window": "覆盖观察窗口",
            "policy_section": "政策来源",
            "ai_section": "AI 来源",
            "summary_total": "总来源",
            "summary_enabled": "启用中",
            "summary_warning": "报错中",
            "summary_stale": "近期静默",
            "summary_empty": "尚无数据",
            "summary_policy": "政策来源数",
            "summary_ai": "AI 来源数",
            "track_official": "主源 / 官方",
            "track_broad": "泛新闻 / 社区",
            "track_supplement": "公众号补充",
            "health_healthy": "正常产出",
            "health_warning": "需要排障",
            "health_stale": "近期静默",
            "health_empty": "尚无沉淀",
            "health_disabled": "已禁用",
            "quality": "来源质量",
            "quality_high": "高质量",
            "quality_medium": "中等质量",
            "quality_low": "待加强",
            "gap_title": "补源提示",
            "gap_ai": "AI 覆盖偏弱主题",
            "gap_policy": "政策覆盖偏弱主题",
            "gap_official": "主源条数",
            "gap_reason_official_gap": "当前只有泛新闻或补充源，没有主源。",
            "gap_reason_signal_skew": "主源覆盖偏弱，补充层信号更强。",
            "meta_type": "采集类型",
            "meta_priority": "优先级",
            "meta_last_seen": "最近入库",
            "meta_recent": "窗口内条数",
            "meta_total": "累计条数",
            "meta_warning": "最近告警",
            "open_source": "打开来源",
            "open_library": "按此来源查看内容",
        }
    return {
        "nav_label": "Source Coverage",
        "title": "Source Coverage | AI Policy Intel",
        "eyebrow": "Source Coverage",
        "heading": "Make source health, silence, and collector failures visible on the page.",
        "note": "This page is for judging whether the collection layer is missing sources, carrying dead feeds, or over-relying on the same channel.",
        "preview_title": "Sources that need attention",
        "preview_eyebrow": "Coverage Preview",
        "preview_note": "Erroring, silent, or still-empty sources rise to the top so you can inspect the collection layer quickly.",
        "warning_title": "Recent collector warnings",
        "warning_eyebrow": "Collector Warnings",
        "warning_empty": "No collector warnings in the recent run window.",
        "open_full": "Open full source page",
        "back_home": "Back home",
        "coverage_window": "Coverage window",
        "policy_section": "Policy sources",
        "ai_section": "AI sources",
        "summary_total": "Total sources",
        "summary_enabled": "Enabled",
        "summary_warning": "With warnings",
        "summary_stale": "Recently silent",
        "summary_empty": "Still empty",
        "summary_policy": "Policy sources",
        "summary_ai": "AI sources",
        "track_official": "Primary / official",
        "track_broad": "Broad / community",
        "track_supplement": "Wechat supplement",
        "health_healthy": "Healthy",
        "health_warning": "Needs attention",
        "health_stale": "Recently silent",
        "health_empty": "No data yet",
        "health_disabled": "Disabled",
        "quality": "Source quality",
        "quality_high": "High quality",
        "quality_medium": "Medium quality",
        "quality_low": "Needs work",
        "gap_title": "Coverage hints",
        "gap_ai": "Weak AI topic coverage",
        "gap_policy": "Weak policy topic coverage",
        "gap_official": "Primary-source items",
        "gap_reason_official_gap": "Only broad or supplement signals exist right now; no primary source items matched.",
        "gap_reason_signal_skew": "Primary coverage looks thin while supplement signals are stronger.",
        "meta_type": "Collector type",
        "meta_priority": "Priority",
        "meta_last_seen": "Last item",
        "meta_recent": "Items in window",
        "meta_total": "Total items",
        "meta_warning": "Latest warning",
        "open_source": "Open source",
        "open_library": "Browse items from this source",
    }



def _ops_ui(lang: str) -> dict[str, str]:
    if lang == "zh":
        return {
            "sources_nav": "来源管理",
            "runs_nav": "运行审计",
            "sources_title": "来源管理 | AI Policy Intel",
            "sources_eyebrow": "来源管理",
            "sources_heading": "直接管控哪些源开启、哪些源需要降权或提权。",
            "sources_note": "这里的修改会实际写回 configs/sources.yaml，下一次采集也会直接生效。",
            "saved": "来源配置已保存。",
            "enable": "启用采集",
            "priority": "优先级",
            "max_results": "每次拉取条数",
            "query": "查询表达式",
            "extra_keys": "extra 字段",
            "save": "保存来源",
            "view_source": "查看来源页",
            "view_library": "按来源查内容",
            "runs_title": "运行审计 | AI Policy Intel",
            "runs_eyebrow": "运行审计",
            "runs_heading": "看每次采集、推送、运维告警、备份是什么结果。",
            "runs_note": "有问题时，优先看 daily / daily-delivery / daily-ops / backup 这几类记录。",
            "backup_title": "备份归档",
            "backup_empty": "还没有备份文件。",
            "log_empty": "暂无运行记录。",
            "message": "详细信息",
            "download_hint": "备份文件目前存在服务器 data/backups/ 下。",
        }
    return {
        "sources_nav": "Source Admin",
        "runs_nav": "Run Audit",
        "sources_title": "Source Admin | AI Policy Intel",
        "sources_eyebrow": "Source Admin",
        "sources_heading": "Control which feeds are enabled and which sources should carry more or less weight.",
        "sources_note": "Changes here are written back to configs/sources.yaml so the next collector run uses them directly.",
        "saved": "Source settings saved.",
        "enable": "Enable collection",
        "priority": "Priority",
        "max_results": "Items per run",
        "query": "Query",
        "extra_keys": "Extra keys",
        "save": "Save source",
        "view_source": "Open source page",
        "view_library": "Open filtered library",
        "runs_title": "Run Audit | AI Policy Intel",
        "runs_eyebrow": "Run Audit",
        "runs_heading": "Inspect how each collection, delivery, ops alert, and backup run behaved.",
        "runs_note": "When something looks off, start with daily, daily-delivery, daily-ops, and backup entries.",
        "backup_title": "Backup Archives",
        "backup_empty": "No backup archives yet.",
        "log_empty": "No run logs yet.",
        "message": "Details",
        "download_hint": "Backup files currently live under data/backups/ on the server.",
    }



def _admin_ui(lang: str) -> dict[str, str]:
    if lang == "zh":
        return {
            "title": "人工校正台 | AI Policy Intel",
            "eyebrow": "人工校正台",
            "heading": "给政策生命周期加一层人工覆盖，避免自动刷新把手工判断冲掉。",
            "note": "本机访问可直接使用；远程访问需要带上 admin_token。",
            "back_policy": "返回政策库",
            "matches": "当前命中",
            "search_placeholder": "搜政策标题、替代关系、人工备注",
            "all_statuses": "全部状态",
            "all_modes": "全部模式",
            "manual_only": "仅人工覆盖",
            "auto_only": "仅自动判断",
            "manual_badge": "人工覆盖",
            "manual_active": "人工覆盖中",
            "override_note": "人工备注",
            "edit_label": "进入编辑",
            "open_label": "打开",
            "empty": "当前筛选下没有条目。",
            "saved": "人工覆盖已保存。",
            "current_result": "当前生效结果",
            "manual_form": "人工覆盖表单",
            "enable_override": "启用人工覆盖",
            "override_placeholder": "写明为什么要人工修正这条政策生命周期",
            "save": "保存覆盖",
            "clear": "清除覆盖",
            "open_detail": "查看详情页",
            "navigation": "导航",
            "next_actions": "继续操作",
            "back_list": "返回校正列表",
            "open_public": "查看普通详情页",
            "open_source": "打开原文",
        }
    return {
        "title": "Lifecycle Overrides | AI Policy Intel",
        "eyebrow": "Lifecycle Overrides",
        "heading": "Apply durable manual overrides so lifecycle refresh does not wipe operator decisions.",
        "note": "Local access works directly. Remote access requires admin_token.",
        "back_policy": "Back to policy library",
        "matches": "Matches",
        "search_placeholder": "Search policy title, replacement, or override note",
        "all_statuses": "All statuses",
        "all_modes": "All modes",
        "manual_only": "Manual only",
        "auto_only": "Auto only",
        "manual_badge": "Manual override",
        "manual_active": "Manual override active",
        "override_note": "Override note",
        "edit_label": "Edit override",
        "open_label": "Open",
        "empty": "No items matched the current filters.",
        "saved": "Manual override saved.",
        "current_result": "Current effective lifecycle",
        "manual_form": "Manual override form",
        "enable_override": "Enable manual override",
        "override_placeholder": "Explain why this lifecycle needs manual correction",
        "save": "Save override",
        "clear": "Clear override",
        "open_detail": "Open detail page",
        "navigation": "Navigation",
        "next_actions": "Next actions",
        "back_list": "Back to override list",
        "open_public": "Open public detail page",
        "open_source": "Open source page",
    }


def _template_context(request: Request, lang: str, active_nav: str, admin_token: str = "", **extra: Any) -> dict[str, Any]:
    current_lang = normalize_lang(lang)
    query = dict(request.query_params)
    if admin_token:
        query["admin_token"] = admin_token
    switch_urls = {
        code: str(request.url.replace_query_params(**{**query, "lang": code}))
        for code in ("zh", "en")
    }
    return {
        "request": request,
        "app_name": settings.app_name,
        "active_nav": active_nav,
        "lang": current_lang,
        "lang_attr": "zh-CN" if current_lang == "zh" else "en",
        "t": get_translator(current_lang),
        "switch_urls": switch_urls,
        "admin_token": admin_token,
        **extra,
    }


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui?lang=zh", status_code=302)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/summary")
def summary() -> dict[str, int]:
    with SessionLocal() as session:
        source_count = session.scalar(select(func.count()).select_from(Source)) or 0
        item_count = session.scalar(select(func.count()).select_from(Item)) or 0
        run_count = session.scalar(select(func.count()).select_from(RunLog)) or 0
    return {"sources": int(source_count), "items": int(item_count), "runs": int(run_count)}


@app.get("/ui", response_class=HTMLResponse)
def ui_dashboard(request: Request, lang: str = Query(default="zh"), admin_token: str = Query(default="")):
    current_lang = normalize_lang(lang)
    context = _dashboard_context(current_lang, admin_token)
    return templates.TemplateResponse(request, "dashboard.html", _template_context(request, current_lang, "dashboard", admin_token, sources_ui=_sources_ui(current_lang), **context))


@app.get("/ui/policies", response_class=HTMLResponse)
def ui_policies(
    request: Request,
    lang: str = Query(default="zh"),
    admin_token: str = Query(default=""),
    q: str = Query(default=""),
    source_id: str = Query(default=""),
    category: str = Query(default=""),
    track: str = Query(default="all"),
    days: int = Query(default=365),
    status: str = Query(default="all"),
):
    current_lang = normalize_lang(lang)
    result = _library_rows("policy", q, source_id, category, track, days, status, current_lang, admin_token)
    filters = {"q": q, "source_id": source_id, "category": category, "track": track, "days": days, "status": status}
    return templates.TemplateResponse(request, "policies.html", _template_context(request, current_lang, "policies", admin_token, filters=filters, policy_ui=_policy_ui(current_lang), sources_ui=_sources_ui(current_lang), **result))


@app.get("/ui/ai", response_class=HTMLResponse)
def ui_ai(
    request: Request,
    lang: str = Query(default="zh"),
    admin_token: str = Query(default=""),
    q: str = Query(default=""),
    source_id: str = Query(default=""),
    category: str = Query(default=""),
    track: str = Query(default="all"),
    days: int = Query(default=90),
):
    current_lang = normalize_lang(lang)
    result = _library_rows("ai", q, source_id, category, track, days, "all", current_lang, admin_token)
    filters = {"q": q, "source_id": source_id, "category": category, "track": track, "days": days}
    return templates.TemplateResponse(request, "ai.html", _template_context(request, current_lang, "ai", admin_token, filters=filters, sources_ui=_sources_ui(current_lang), **result))


@app.get("/ui/sources", response_class=HTMLResponse)
def ui_sources(
    request: Request,
    lang: str = Query(default="zh"),
    admin_token: str = Query(default=""),
    days: int = Query(default=7),
):
    current_lang = normalize_lang(lang)
    normalized_days = days if days in {3, 7, 14, 30} else 7
    result = _source_overview_rows(current_lang, admin_token, window=normalized_days, preview_limit=24)
    gaps = _gap_hint_rows(current_lang, admin_token, window=normalized_days)
    filters = {"days": normalized_days}
    return templates.TemplateResponse(request, "sources.html", _template_context(request, current_lang, "dashboard", admin_token, filters=filters, sources_ui=_sources_ui(current_lang), **result, **gaps))


@app.get("/ui/items/{item_id}", response_class=HTMLResponse)
def ui_item_detail(request: Request, item_id: int, lang: str = Query(default="zh"), admin_token: str = Query(default="")):
    current_lang = normalize_lang(lang)
    with SessionLocal() as session:
        source_map = _load_sources(session)
        item = session.get(Item, item_id)
        if item is None:
            context = _template_context(request, current_lang, "dashboard", admin_token, item_id=item_id)
            return templates.TemplateResponse(request, "missing.html", context, status_code=404)
        related_pool = session.execute(select(Item).where(Item.id != item.id).order_by(Item.fetched_at.desc()).limit(120)).scalars().all()
        kind = "policy" if _is_policy(item) else "ai"
        topic_name = _topic_name_for_item(item, kind)
        related = [
            row for row in related_pool
            if ((_is_policy(row) if kind == "policy" else _is_ai(row)) and (_topic_name_for_item(row, kind) == topic_name or row.source_id == item.source_id))
        ][:18]
        policy_context = _policy_detail_context(session, item, source_map, current_lang, admin_token)

    card = _build_card(item, source_map.get(item.source_id), current_lang, admin_token)
    related_cards = [_build_card(row, source_map.get(row.source_id), current_lang, admin_token) for row in related]
    related_groups = _group_same_event_cards(related_cards, limit=6)
    active_nav = "policies" if card["kind"] == "policy" else "ai"
    context = _template_context(request, current_lang, active_nav, admin_token, item=card, related_items=related_groups, detail_ui=_detail_ui(current_lang), policy_ui=_policy_ui(current_lang), sources_ui=_sources_ui(current_lang), **policy_context)
    return templates.TemplateResponse(request, "detail.html", context)


@app.get("/ui/admin/policies", response_class=HTMLResponse)
def ui_admin_policies(
    request: Request,
    lang: str = Query(default="zh"),
    admin_token: str = Query(default=""),
    q: str = Query(default=""),
    source_id: str = Query(default=""),
    status: str = Query(default="all"),
    override: str = Query(default="all"),
    days: int = Query(default=365),
):
    _require_admin_access(request, admin_token)
    current_lang = normalize_lang(lang)
    normalized_status = status if status in {"all", *POLICY_STATUSES} else "all"
    normalized_override = override if override in OVERRIDE_FILTERS else "all"
    result = _admin_rows(q, source_id, normalized_status, normalized_override, days, current_lang, admin_token)
    filters = {"q": q, "source_id": source_id, "status": normalized_status, "override": normalized_override, "days": days}
    context = _template_context(request, current_lang, "policies", admin_token, filters=filters, admin_ui=_admin_ui(current_lang), queue_ui=_queue_ui(current_lang), relation_ui=_relation_ui(current_lang), ops_ui=_ops_ui(current_lang), **result)
    return templates.TemplateResponse(request, "admin_policies.html", context)


@app.get("/ui/admin/policies/{item_id}", response_class=HTMLResponse)
def ui_admin_policy_detail(request: Request, item_id: int, lang: str = Query(default="zh"), admin_token: str = Query(default=""), saved: int = Query(default=0)):
    _require_admin_access(request, admin_token)
    current_lang = normalize_lang(lang)
    with SessionLocal() as session:
        source_map = _load_sources(session)
        item = session.get(Item, item_id)
        if item is None:
            context = _template_context(request, current_lang, "policies", admin_token, item_id=item_id)
            return templates.TemplateResponse(request, "missing.html", context, status_code=404)
    card = _build_card(item, source_map.get(item.source_id), current_lang, admin_token)
    context = _template_context(request, current_lang, "policies", admin_token, item=card, saved=bool(saved), admin_ui=_admin_ui(current_lang), queue_ui=_queue_ui(current_lang), relation_ui=_relation_ui(current_lang), ops_ui=_ops_ui(current_lang))
    return templates.TemplateResponse(request, "admin_policy_edit.html", context)


@app.post("/ui/admin/policies/{item_id}")
def ui_admin_policy_update(
    request: Request,
    item_id: int,
    lang: str = Form(default="zh"),
    admin_token: str = Form(default=""),
    override_enabled: str = Form(default=""),
    override_status: str = Form(default="unknown"),
    override_effective_at: str = Form(default=""),
    override_expires_at: str = Form(default=""),
    override_replaced_by: str = Form(default=""),
    override_reason: str = Form(default=""),
    action: str = Form(default="save"),
):
    _require_admin_access(request, admin_token)
    current_lang = normalize_lang(lang)
    with SessionLocal() as session:
        item = session.get(Item, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Policy item not found")

        if action == "clear":
            clear_manual_override(item)
        else:
            item.override_enabled = override_enabled == "on"
            if item.override_enabled:
                item.override_status = override_status if override_status in POLICY_STATUSES else (item.status or "unknown")
                item.override_effective_at = parse_admin_date(override_effective_at)
                item.override_expires_at = parse_admin_date(override_expires_at)
                item.override_replaced_by = override_replaced_by.strip()
                item.override_reason = override_reason.strip()
                item.override_updated_at = datetime.utcnow()
            else:
                clear_manual_override(item)

        apply_policy_lifecycle(item)
        session.commit()

    return RedirectResponse(
        url=_build_path(f"/ui/admin/policies/{item_id}", lang=current_lang, admin_token=admin_token, saved=1),
        status_code=303,
    )



@app.get("/ui/admin/policy-queue", response_class=HTMLResponse)
def ui_admin_policy_queue(
    request: Request,
    lang: str = Query(default="zh"),
    admin_token: str = Query(default=""),
    window: int = Query(default=30),
):
    _require_admin_access(request, admin_token)
    current_lang = normalize_lang(lang)
    normalized_window = max(1, min(window, 180))
    result = _queue_rows(normalized_window, current_lang, admin_token)
    context = _template_context(
        request,
        current_lang,
        "policies",
        admin_token,
        queue_ui=_queue_ui(current_lang),
        relation_ui=_relation_ui(current_lang),
        admin_ui=_admin_ui(current_lang),
        ops_ui=_ops_ui(current_lang),
        **result,
    )
    return templates.TemplateResponse(request, "admin_policy_queue.html", context)


@app.get("/ui/admin/policy-relations", response_class=HTMLResponse)
def ui_admin_policy_relations(
    request: Request,
    lang: str = Query(default="zh"),
    admin_token: str = Query(default=""),
):
    _require_admin_access(request, admin_token)
    current_lang = normalize_lang(lang)
    result = _relation_rows(current_lang, admin_token)
    context = _template_context(
        request,
        current_lang,
        "policies",
        admin_token,
        queue_ui=_queue_ui(current_lang),
        relation_ui=_relation_ui(current_lang),
        admin_ui=_admin_ui(current_lang),
        ops_ui=_ops_ui(current_lang),
        **result,
    )
    return templates.TemplateResponse(request, "admin_policy_relations.html", context)



def _admin_source_rows(lang: str, admin_token: str) -> dict[str, object]:
    overview = _source_overview_rows(lang, admin_token, window=max(settings.collector_stale_days, 3), preview_limit=200)
    definitions = load_source_definitions(settings.config_dir / 'sources.yaml')
    definition_map = {definition.id: definition for definition in definitions}
    items: list[dict[str, object]] = []
    for row in [*overview['policy_sources'], *overview['ai_sources']]:
        definition = definition_map.get(row['id'])
        if definition is None:
            continue
        item = dict(row)
        item['max_results'] = definition.max_results
        item['query'] = definition.query
        item['extra_keys'] = sorted(definition.extra.keys())
        items.append(item)
    return {
        'items': items,
        'count': len(items),
        'source_summary': overview['source_summary'],
        'collector_warnings': overview['collector_warnings'][:10],
    }



def _audit_rows(lang: str, admin_token: str) -> dict[str, object]:
    with SessionLocal() as session:
        rows = session.execute(select(RunLog).order_by(RunLog.created_at.desc()).limit(80)).scalars().all()
    backups = sorted(settings.backup_dir.glob('*.tar.gz'), key=lambda path: path.stat().st_mtime, reverse=True)
    return {
        'runs': [
            {
                'run_type': row.run_type,
                'status': row.status,
                'created_at': row.created_at,
                'message': row.message or '',
                'tone': 'good' if row.status == 'success' else 'warning' if row.status in {'warning', 'partial'} else 'neutral',
            }
            for row in rows
        ],
        'backups': [
            {
                'name': path.name,
                'size_mb': f"{path.stat().st_size / 1024 / 1024:.1f}",
                'modified_at': datetime.fromtimestamp(path.stat().st_mtime),
            }
            for path in backups[:24]
        ],
    }



@app.get('/ui/admin/sources', response_class=HTMLResponse)
def ui_admin_sources(request: Request, lang: str = Query(default='zh'), admin_token: str = Query(default=''), saved: int = Query(default=0)):
    _require_admin_access(request, admin_token)
    current_lang = normalize_lang(lang)
    result = _admin_source_rows(current_lang, admin_token)
    context = _template_context(request, current_lang, 'dashboard', admin_token, saved=bool(saved), ops_ui=_ops_ui(current_lang), sources_ui=_sources_ui(current_lang), **result)
    return templates.TemplateResponse(request, 'admin_sources.html', context)



@app.post('/ui/admin/sources/{source_id}')
def ui_admin_source_update(
    request: Request,
    source_id: str,
    lang: str = Form(default='zh'),
    admin_token: str = Form(default=''),
    enabled: str = Form(default=''),
    priority: int = Form(default=5),
    max_results: int = Form(default=10),
):
    _require_admin_access(request, admin_token)
    current_lang = normalize_lang(lang)
    update_source_definition(
        settings.config_dir / 'sources.yaml',
        source_id,
        enabled=enabled == 'on',
        priority=max(1, min(priority, 20)),
        max_results=max(1, min(max_results, 50)),
    )
    definitions = load_source_definitions(settings.config_dir / 'sources.yaml')
    with SessionLocal() as session:
        sync_sources(session, definitions)
    return RedirectResponse(url=_build_path('/ui/admin/sources', lang=current_lang, admin_token=admin_token, saved=1), status_code=303)



@app.get('/ui/admin/runs', response_class=HTMLResponse)
def ui_admin_runs(request: Request, lang: str = Query(default='zh'), admin_token: str = Query(default='')):
    _require_admin_access(request, admin_token)
    current_lang = normalize_lang(lang)
    result = _audit_rows(current_lang, admin_token)
    context = _template_context(request, current_lang, 'dashboard', admin_token, ops_ui=_ops_ui(current_lang), **result)
    return templates.TemplateResponse(request, 'admin_runs.html', context)


@app.get("/ui/ai/topics", response_class=HTMLResponse)
def ui_ai_topics(request: Request, lang: str = Query(default="zh"), admin_token: str = Query(default=""), days: int = Query(default=90), track: str = Query(default="all"), sort: str = Query(default="hot")):
    current_lang = normalize_lang(lang)
    normalized_days = days if days in {7, 30, 90, 180, 365} else 90
    normalized_track = track if track in {"all", "official", "broad", "supplement"} else "all"
    normalized_sort = sort if sort in {"hot", "rising", "coverage"} else "hot"
    result = _topic_cluster_rows("ai", current_lang, admin_token, normalized_days, normalized_track, normalized_sort)
    filters = {"days": normalized_days, "track": normalized_track, "sort": normalized_sort}
    return templates.TemplateResponse(request, "topic_clusters.html", _template_context(request, current_lang, "ai", admin_token, filters=filters, topics_ui=_topics_ui("ai", current_lang), library_path=_build_path("/ui/ai", lang=current_lang, admin_token=admin_token), **result))


@app.get("/ui/policies/topics", response_class=HTMLResponse)
def ui_policy_topics(request: Request, lang: str = Query(default="zh"), admin_token: str = Query(default=""), days: int = Query(default=365), track: str = Query(default="all"), sort: str = Query(default="hot")):
    current_lang = normalize_lang(lang)
    normalized_days = days if days in {7, 30, 90, 180, 365} else 365
    normalized_track = track if track in {"all", "official", "broad", "supplement"} else "all"
    normalized_sort = sort if sort in {"hot", "rising", "coverage"} else "hot"
    result = _topic_cluster_rows("policy", current_lang, admin_token, normalized_days, normalized_track, normalized_sort)
    filters = {"days": normalized_days, "track": normalized_track, "sort": normalized_sort}
    return templates.TemplateResponse(request, "topic_clusters.html", _template_context(request, current_lang, "policies", admin_token, filters=filters, topics_ui=_topics_ui("policy", current_lang), library_path=_build_path("/ui/policies", lang=current_lang, admin_token=admin_token), **result))


def _policy_audit_ui(lang: str) -> dict[str, str]:
    if lang == "zh":
        return {
            "label": "\u81ea\u52a8\u6821\u9a8c",
            "unknown_status": "\u72b6\u6001\u5f85\u6838\u9a8c",
            "stale_check": "\u8d85\u8fc7 14 \u5929\u672a\u590d\u6838",
            "missing_successor": "\u5df2\u6807\u8bb0\u66ff\u4ee3\u4f46\u672a\u5339\u914d\u5230\u65b0\u653f\u7b56",
            "future_effective": "\u751f\u6548\u65f6\u95f4\u4ecd\u5728\u672a\u6765",
            "expired_status_mismatch": "\u5df2\u8fc7\u671f\u4f46\u72b6\u6001\u672a\u5207\u5230\u5931\u6548",
            "draft_stale": "\u5f81\u6c42\u610f\u89c1\u7a3f\u957f\u671f\u672a\u66f4\u65b0",
            "long_running_no_expiry": "\u957f\u671f\u6709\u6548\u653f\u7b56\u7f3a\u5c11\u5230\u671f\u4fe1\u606f",
            "inactive_without_signal": "\u5df2\u5931\u6548\u4f46\u7f3a\u5c11\u660e\u786e\u5931\u6548\u4fe1\u53f7",
        }
    return {
        "label": "Auto check",
        "unknown_status": "Unknown lifecycle status",
        "stale_check": "Not rechecked in 14+ days",
        "missing_successor": "Marked superseded but successor not found",
        "future_effective": "Effective date is still in the future",
        "expired_status_mismatch": "Expired but status is not inactive",
        "draft_stale": "Draft item has gone stale",
        "long_running_no_expiry": "Long-running official policy has no expiry",
        "inactive_without_signal": "Inactive status lacks a clear expiry signal",
    }


def _policy_audit_summary(item: Item, lang: str, successor_exists: bool = False) -> dict[str, object]:
    from app.policy_lifecycle import audit_policy_lifecycle

    ui = _policy_audit_ui(lang)
    audit = audit_policy_lifecycle(item, successor_exists=successor_exists)
    codes = list(audit.get("codes", []))
    labels = [ui.get(code, code) for code in codes]
    primary_code = codes[0] if codes else ""
    return {
        "codes": codes,
        "labels": labels,
        "count": len(codes),
        "tone": str(audit.get("tone", "neutral")),
        "needs_review": bool(codes),
        "primary_code": primary_code,
        "primary_label": ui.get(primary_code, ""),
    }


def _build_card(item: Item, source: Source | None, lang: str, admin_token: str = "", policy_audit: dict[str, object] | None = None) -> dict[str, object]:
    policy_signal = _policy_signal(item) if _is_policy(item) else None
    lifecycle = _policy_lifecycle_state(item, lang) if _is_policy(item) else None
    kind = "policy" if _is_policy(item) else "ai" if _is_ai(item) else "other"
    return {
        "id": item.id,
        "title": item.title,
        "url": item.url,
        "detail_path": _build_path(f"/ui/items/{item.id}", lang=lang, admin_token=admin_token),
        "admin_path": _build_path(f"/ui/admin/policies/{item.id}", lang=lang, admin_token=admin_token),
        "category": item.category,
        "source_id": item.source_id,
        "source_name": source.name if source else item.source_id,
        "published_at": item.published_at,
        "fetched_at": item.fetched_at,
        "effective_at": item.effective_at,
        "expires_at": item.expires_at,
        "status": item.status or "unknown",
        "replaced_by": item.replaced_by or "",
        "last_checked_at": item.last_checked_at,
        "status_reason": item.status_reason or "",
        "score": item.score,
        "summary": _summary_text(item),
        "reason": item.reason,
        "tags": _item_tags(item, source),
        "region": item.region,
        "override_enabled": bool(item.override_enabled),
        "override_status": item.override_status or "",
        "override_effective_at": item.override_effective_at,
        "override_expires_at": item.override_expires_at,
        "override_replaced_by": item.override_replaced_by or "",
        "override_reason": item.override_reason or "",
        "override_updated_at": item.override_updated_at,
        "policy_signal": {
            "code": policy_signal["code"],
            "tone": policy_signal["tone"],
            "label": translate(lang, f"policy.signal.{policy_signal['code']}"),
        } if policy_signal else None,
        "lifecycle": lifecycle,
        "kind": kind,
        "kind_label": translate(lang, f"kind.{kind}"),
        "policy_audit": policy_audit if policy_audit is not None else (_policy_audit_summary(item, lang) if _is_policy(item) else None),
        "event_key": _same_event_key(item),
    }


def _queue_rows(window: int, lang: str, admin_token: str) -> dict[str, object]:
    with SessionLocal() as session:
        source_map = _load_sources(session)
        rows = _load_recent_items(session, limit=1800)

    policies = [row for row in rows if _is_policy(row)]
    now = datetime.utcnow()
    future = now + timedelta(days=window)
    title_set = {(row.title or "").strip() for row in policies if (row.title or "").strip()}
    audit_map = {
        row.id: _policy_audit_summary(
            row,
            lang,
            successor_exists=bool((row.replaced_by or "").strip() and (row.replaced_by or "").strip() in title_set),
        )
        for row in policies
    }

    unknown_rows = sorted(
        [row for row in policies if (row.status or "unknown") == "unknown"],
        key=lambda row: (_sort_dt(row), row.score or 0.0),
        reverse=True,
    )
    expiring_rows = sorted(
        [
            row for row in policies
            if row.expires_at
            and now <= row.expires_at <= future
            and (row.status or "unknown") not in {"inactive", "superseded"}
        ],
        key=lambda row: (row.expires_at or datetime.max, -1 * (row.score or 0.0)),
    )
    overdue_rows = sorted(
        [
            row for row in policies
            if row.expires_at
            and row.expires_at < now
            and (row.status or "unknown") != "inactive"
        ],
        key=lambda row: (row.expires_at or datetime.min, -1 * (row.score or 0.0)),
    )
    excluded_ids = {row.id for row in [*unknown_rows, *expiring_rows, *overdue_rows]}
    recheck_rows = sorted(
        [row for row in policies if row.id not in excluded_ids and bool(audit_map[row.id]["needs_review"])],
        key=lambda row: (
            0 if audit_map[row.id]["tone"] == "warning" else 1,
            -int(audit_map[row.id]["count"]),
            -int((row.score or 0.0) * 10),
            -int(_sort_dt(row).timestamp()) if _sort_dt(row) != datetime.min else 0,
        ),
    )

    def build_queue_card(row: Item) -> dict[str, object]:
        return _build_card(row, source_map.get(row.source_id), lang, admin_token, policy_audit=audit_map[row.id])

    return {
        "window": window,
        "counts": {
            "unknown": len(unknown_rows),
            "recheck": len(recheck_rows),
            "expiring": len(expiring_rows),
            "overdue": len(overdue_rows),
        },
        "unknown_items": [build_queue_card(row) for row in unknown_rows[:80]],
        "recheck_items": [build_queue_card(row) for row in recheck_rows[:80]],
        "expiring_items": [build_queue_card(row) for row in expiring_rows[:80]],
        "overdue_items": [build_queue_card(row) for row in overdue_rows[:80]],
    }


def _topics_ui(kind: str, lang: str) -> dict[str, str]:
    if lang == "zh":
        base = {
            "ai": {
                "nav": "AI \u4e3b\u9898\u9875",
                "title": "AI \u4e3b\u9898\u9875 | AI Policy Intel",
                "eyebrow": "AI \u4e3b\u9898\u805a\u5408",
                "heading": "\u628a\u96f6\u6563\u65b0\u95fb\u4e32\u6210\u53ef\u56de\u770b\u7684\u4e3b\u9898\u7c07\u3002",
                "note": "\u66f4\u9002\u5408\u770b\u6a21\u578b\u3001\u516c\u53f8\u3001\u5f00\u6e90\u9879\u76ee\u548c\u7814\u7a76\u65b9\u5411\u5728\u6700\u8fd1\u4e00\u6bb5\u65f6\u95f4\u91cc\u7684\u8fde\u7eed\u53d8\u5316\u3002",
            },
            "policy": {
                "nav": "\u653f\u7b56\u4e3b\u9898\u9875",
                "title": "\u653f\u7b56\u4e3b\u9898\u9875 | AI Policy Intel",
                "eyebrow": "\u653f\u7b56\u4e3b\u9898\u805a\u5408",
                "heading": "\u628a\u653f\u7b56\u6309\u4e3b\u9898\u800c\u4e0d\u662f\u6309\u5355\u6761\u516c\u544a\u67e5\u770b\u3002",
                "note": "\u66f4\u9002\u5408\u56de\u770b\u67d0\u4e2a\u76d1\u7ba1\u4e3b\u9898\u3001\u6276\u6301\u65b9\u5411\u6216\u6cbb\u7406\u8bae\u9898\u5728\u6700\u8fd1\u4e00\u6bb5\u65f6\u95f4\u91cc\u7684\u8fde\u7eed\u53d8\u5316\u3002",
            },
        }
        shared = {
            "window": "\u89c2\u5bdf\u7a97\u53e3",
            "matches": "\u4e3b\u9898\u6570",
            "official": "\u4e3b\u6e90 / \u5b98\u65b9",
            "broad": "\u6cdb\u65b0\u95fb / \u793e\u533a",
            "supplement": "\u8865\u5145\u6e90",
            "sources": "\u6765\u6e90\u6570",
            "latest": "\u6700\u8fd1\u52a8\u6001",
            "empty": "\u5f53\u524d\u7a97\u53e3\u5185\u8fd8\u6ca1\u6709\u53ef\u5c55\u793a\u7684\u4e3b\u9898\u7c07\u3002",
            "open_library": "\u56de\u5230\u5217\u8868\u9875",
            "source_coverage": "\u6765\u6e90\u8986\u76d6",
            "trend": "\u8d8b\u52bf\u53d8\u5316",
            "history_link": "\u5386\u53f2\u8d8b\u52bf",
            "track_all": "\u5168\u90e8\u6765\u6e90",
            "track_official": "\u4e3b\u6e90 / \u5b98\u65b9",
            "track_broad": "\u6cdb\u65b0\u95fb / \u793e\u533a",
            "track_supplement": "\u8865\u5145\u6e90",
            "sort_hot": "\u6309\u70ed\u5ea6",
            "sort_rising": "\u6309\u4e0a\u5347",
            "sort_coverage": "\u6309\u8986\u76d6",
            "same_event": "\u540c\u4e8b\u4ef6\u6765\u6e90",
            "extra_sources": "\u66f4\u591a\u6765\u6e90",
        }
    else:
        base = {
            "ai": {"nav": "AI Topics", "title": "AI Topics | AI Policy Intel", "eyebrow": "AI Topic Clusters", "heading": "Turn scattered items into revisit-friendly topic clusters.", "note": "This view is better for tracking models, companies, OSS projects, and research directions over time."},
            "policy": {"nav": "Policy Topics", "title": "Policy Topics | AI Policy Intel", "eyebrow": "Policy Topic Clusters", "heading": "Review policies by theme instead of only by individual notices.", "note": "This view is better for following how a regulatory topic or support direction evolves over time."},
        }
        shared = {
            "window": "Window",
            "matches": "Topics",
            "official": "Primary / official",
            "broad": "Broad / community",
            "supplement": "Supplement",
            "sources": "Sources",
            "latest": "Latest update",
            "empty": "No topic clusters matched the current window.",
            "open_library": "Back to library",
            "source_coverage": "Source Coverage",
            "trend": "Trend",
            "history_link": "History",
            "track_all": "All sources",
            "track_official": "Primary / official",
            "track_broad": "Broad / community",
            "track_supplement": "Supplement",
            "sort_hot": "Sort by heat",
            "sort_rising": "Sort by rise",
            "sort_coverage": "Sort by coverage",
            "same_event": "Same event sources",
            "extra_sources": "More sources",
        }
    return {**base[kind], **shared}


def _topic_history_ui(kind: str, lang: str) -> dict[str, str]:
    if lang == "zh":
        base = {
            "ai": {
                "title": "AI \u5386\u53f2\u8d8b\u52bf | AI Policy Intel",
                "eyebrow": "AI \u5386\u53f2\u8d8b\u52bf",
                "heading": "\u628a\u4e3b\u9898\u70ed\u5ea6\u653e\u5230\u65f6\u95f4\u8f74\u4e0a\u770b\u3002",
                "note": "\u8fd9\u9875\u66f4\u9002\u5408\u5224\u65ad\u67d0\u4e2a AI \u4e3b\u9898\u662f\u5728\u6301\u7eed\u5347\u6e29\uff0c\u8fd8\u662f\u53ea\u662f\u77ed\u65f6\u566a\u58f0\u3002",
            },
            "policy": {
                "title": "\u653f\u7b56\u5386\u53f2\u8d8b\u52bf | AI Policy Intel",
                "eyebrow": "\u653f\u7b56\u5386\u53f2\u8d8b\u52bf",
                "heading": "\u6309\u65f6\u95f4\u56de\u770b\u653f\u7b56\u4e3b\u9898\u70ed\u5ea6\u3002",
                "note": "\u66f4\u9002\u5408\u5224\u65ad\u67d0\u4e2a\u6cbb\u7406\u8bae\u9898\u662f\u6301\u7eed\u63a8\u8fdb\uff0c\u8fd8\u662f\u5df2\u7ecf\u8f6c\u51b7\u3002",
            },
        }
        shared = {
            "back_clusters": "\u56de\u5230\u4e3b\u9898\u9875",
            "open_library": "\u56de\u5230\u5217\u8868\u9875",
            "source_coverage": "\u6765\u6e90\u8986\u76d6",
            "series": "\u4e3b\u9898\u5e8f\u5217",
            "snapshot": "\u6700\u65b0\u5feb\u7167",
            "latest": "\u5f53\u524d\u6761\u6570",
            "previous": "\u524d\u4e00\u5feb\u7167",
            "trend": "\u8d8b\u52bf",
            "empty": "\u5386\u53f2\u5feb\u7167\u8fd8\u4e0d\u591f\uff0c\u7b49\u7cfb\u7edf\u7ee7\u7eed\u8fd0\u884c\u51e0\u8f6e\u540e\u8fd9\u91cc\u4f1a\u66f4\u6709\u4ef7\u503c\u3002",
            "sort_hot": "\u6309\u5f53\u524d\u70ed\u5ea6",
            "sort_rising": "\u6309\u4e0a\u5347\u5e45\u5ea6",
            "sort_coverage": "\u6309\u6765\u6e90\u8986\u76d6",
            "trend_all": "\u5168\u90e8\u8d8b\u52bf",
            "trend_rising": "\u53ea\u770b\u4e0a\u5347",
            "trend_falling": "\u53ea\u770b\u4e0b\u964d",
            "trend_stable": "\u53ea\u770b\u7a33\u5b9a",
            "official": "\u4e3b\u6e90 / \u5b98\u65b9",
            "broad": "\u6cdb\u65b0\u95fb / \u793e\u533a",
            "supplement": "\u8865\u5145\u6e90",
            "sources": "\u6765\u6e90\u6570",
            "peak": "\u5cf0\u503c",
        }
    else:
        base = {
            "ai": {
                "title": "AI Trend History | AI Policy Intel",
                "eyebrow": "AI Trend History",
                "heading": "Put topic heat on a time axis.",
                "note": "Use this page to judge whether an AI topic is compounding or just flashing briefly.",
            },
            "policy": {
                "title": "Policy Trend History | AI Policy Intel",
                "eyebrow": "Policy Trend History",
                "heading": "Review policy themes over time.",
                "note": "This is better for spotting whether a governance topic is still building or already cooling down.",
            },
        }
        shared = {
            "back_clusters": "Back to topics",
            "open_library": "Back to library",
            "source_coverage": "Source Coverage",
            "series": "Series",
            "snapshot": "Latest snapshot",
            "latest": "Current count",
            "previous": "Previous snapshot",
            "trend": "Trend",
            "empty": "Snapshot history is still thin. This page becomes more valuable after a few more runs.",
            "sort_hot": "Sort by current heat",
            "sort_rising": "Sort by rise",
            "sort_coverage": "Sort by coverage",
            "trend_all": "All trends",
            "trend_rising": "Rising only",
            "trend_falling": "Falling only",
            "trend_stable": "Stable only",
            "official": "Primary / official",
            "broad": "Broad / community",
            "supplement": "Supplement",
            "sources": "Sources",
            "peak": "Peak",
        }
    return {**base[kind], **shared}


@app.get("/ui/ai/topics/history", response_class=HTMLResponse)
def ui_ai_topic_history(request: Request, lang: str = Query(default="zh"), admin_token: str = Query(default=""), days: int = Query(default=90), sort: str = Query(default="hot"), trend: str = Query(default="all")):
    from app.topic_intel import load_topic_history_series

    current_lang = normalize_lang(lang)
    normalized_days = days if days in {7, 30, 90, 180, 365} else 90
    normalized_sort = sort if sort in {"hot", "rising", "coverage"} else "hot"
    normalized_trend = trend if trend in {"all", "rising", "falling", "stable"} else "all"
    with SessionLocal() as session:
        result = load_topic_history_series(session, "ai", normalized_days, lookback=14, limit=18, sort_by=normalized_sort, trend=normalized_trend)
    filters = {"days": normalized_days, "sort": normalized_sort, "trend": normalized_trend}
    context = _template_context(
        request,
        current_lang,
        "ai",
        admin_token,
        filters=filters,
        history_ui=_topic_history_ui("ai", current_lang),
        library_path=_build_path("/ui/ai", lang=current_lang, admin_token=admin_token),
        cluster_path=_build_path("/ui/ai/topics", lang=current_lang, admin_token=admin_token, days=normalized_days),
        **result,
    )
    return templates.TemplateResponse(request, "topic_history.html", context)


@app.get("/ui/policies/topics/history", response_class=HTMLResponse)
def ui_policy_topic_history(request: Request, lang: str = Query(default="zh"), admin_token: str = Query(default=""), days: int = Query(default=365), sort: str = Query(default="hot"), trend: str = Query(default="all")):
    from app.topic_intel import load_topic_history_series

    current_lang = normalize_lang(lang)
    normalized_days = days if days in {7, 30, 90, 180, 365} else 365
    normalized_sort = sort if sort in {"hot", "rising", "coverage"} else "hot"
    normalized_trend = trend if trend in {"all", "rising", "falling", "stable"} else "all"
    with SessionLocal() as session:
        result = load_topic_history_series(session, "policy", normalized_days, lookback=14, limit=18, sort_by=normalized_sort, trend=normalized_trend)
    filters = {"days": normalized_days, "sort": normalized_sort, "trend": normalized_trend}
    context = _template_context(
        request,
        current_lang,
        "policies",
        admin_token,
        filters=filters,
        history_ui=_topic_history_ui("policy", current_lang),
        library_path=_build_path("/ui/policies", lang=current_lang, admin_token=admin_token),
        cluster_path=_build_path("/ui/policies/topics", lang=current_lang, admin_token=admin_token, days=normalized_days),
        **result,
    )
    return templates.TemplateResponse(request, "topic_history.html", context)

