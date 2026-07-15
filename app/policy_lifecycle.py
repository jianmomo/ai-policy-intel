from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Item

OFFICIAL_POLICY_SOURCES = {"gov-policy", "miit-policy", "ndrc-notice"}
BROAD_POLICY_SOURCES = {"gov-news", "miit", "cac-gov"}
OVERRIDE_STATUSES = {"active", "draft", "inactive", "superseded", "unknown"}

DATE_PATTERNS = [
    re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})"),
    re.compile(r"(20\d{2})\u5e74(\d{1,2})\u6708(\d{1,2})\u65e5"),
]

EFFECTIVE_PATTERNS = [
    re.compile(r"\u81ea(20\d{2}\u5e74\d{1,2}\u6708\d{1,2}\u65e5)\u8d77\u65bd\u884c"),
    re.compile(r"\u81ea(20\d{2}-\d{1,2}-\d{1,2})\u8d77\u65bd\u884c"),
]

EXPIRY_PATTERNS = [
    re.compile(r"\u6709\u6548\u671f\u81f3(20\d{2}\u5e74\d{1,2}\u6708\d{1,2}\u65e5)"),
    re.compile(r"\u6709\u6548\u671f\u81f3(20\d{2}-\d{1,2}-\d{1,2})"),
    re.compile(r"\u81f3(20\d{2}\u5e74\d{1,2}\u6708\d{1,2}\u65e5)\u6b62"),
    re.compile(r"\u81f3(20\d{2}-\d{1,2}-\d{1,2})\u6b62"),
]

RETIRE_PATTERNS = [
    re.compile(r"\u5e9f\u6b62\u300a([^\u300b]{3,80})\u300b"),
    re.compile(r"\u300a([^\u300b]{3,80})\u300b\u540c\u65f6\u5e9f\u6b62"),
    re.compile(r"\u66ff\u4ee3\u300a([^\u300b]{3,80})\u300b"),
]

POLICY_TITLE_HINTS = (
    "\u901a\u77e5",
    "\u610f\u89c1",
    "\u65b9\u6848",
    "\u89c4\u5212",
    "\u529e\u6cd5",
    "\u89c4\u5b9a",
    "\u516c\u544a",
    "\u89e3\u8bfb",
    "\u7b54\u8bb0\u8005\u95ee",
)

DRAFT_HINTS = (
    "\u5f81\u6c42\u610f\u89c1",
    "\u5f81\u6c42\u610f\u89c1\u7a3f",
    "\u516c\u5f00\u5f81\u6c42\u610f\u89c1",
)

INACTIVE_HINTS = (
    "\u5e9f\u6b62",
    "\u5931\u6548",
    "\u505c\u6b62\u6267\u884c",
    "\u4e0d\u518d\u65bd\u884c",
    "\u4f5c\u5e9f",
)


def _combined_text(title: str, summary: str, raw_content: str) -> str:
    return " ".join(part for part in [title, summary, raw_content] if part)


def _parse_date_token(token: str) -> datetime | None:
    normalized = token.strip()
    for pattern in DATE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    return None


def parse_admin_date(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError:
        return None


def _extract_date(text: str, patterns: list[re.Pattern[str]]) -> datetime | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            parsed = _parse_date_token(match.group(1))
            if parsed:
                return parsed
    return None


def _is_policy_like(item: Item) -> bool:
    return (
        item.category.startswith("Policy-")
        or item.region == "cn"
        or item.source_id in OFFICIAL_POLICY_SOURCES
        or item.source_id in BROAD_POLICY_SOURCES
        or item.source_id.startswith("wechat-policy-")
        or any(hint in (item.title or "") for hint in POLICY_TITLE_HINTS)
    )


def has_manual_override(item: Item) -> bool:
    return bool(getattr(item, "override_enabled", False))


def detect_policy_lifecycle(item: Item) -> dict[str, Any]:
    now = datetime.utcnow()
    text = _combined_text(item.title or "", item.summary or "", item.raw_content or "")
    status = "unknown"
    reason_bits: list[str] = []

    effective_at = _extract_date(text, EFFECTIVE_PATTERNS)
    expires_at = _extract_date(text, EXPIRY_PATTERNS)

    if any(hint in text for hint in DRAFT_HINTS):
        status = "draft"
        reason_bits.append("keyword:draft")
    if any(hint in text for hint in INACTIVE_HINTS):
        status = "inactive"
        reason_bits.append("keyword:inactive")
    if expires_at and expires_at < now:
        status = "inactive"
        reason_bits.append("expired")

    if status == "unknown":
        if item.replaced_by:
            status = "superseded"
            reason_bits.append("linked:replaced")
        elif item.source_id in OFFICIAL_POLICY_SOURCES or any(hint in (item.title or "") for hint in POLICY_TITLE_HINTS):
            status = "active"
            reason_bits.append("official-or-policy-title")
        elif item.source_id.startswith("wechat-policy-") or item.source_id in BROAD_POLICY_SOURCES:
            reason_bits.append("supplement-or-broad")

    if effective_at is None and status in {"active", "superseded"}:
        effective_at = item.published_at or item.fetched_at
        reason_bits.append("fallback:published_at")

    return {
        "effective_at": effective_at,
        "expires_at": expires_at,
        "status": status,
        "replaced_by": item.replaced_by or "",
        "last_checked_at": now,
        "status_reason": ", ".join(dict.fromkeys(reason_bits)) or "no-signal",
    }


def _extract_retired_titles(text: str) -> list[str]:
    titles: list[str] = []
    for pattern in RETIRE_PATTERNS:
        titles.extend(match.group(1).strip() for match in pattern.finditer(text))
    unique: list[str] = []
    for title in titles:
        if title and title not in unique:
            unique.append(title)
    return unique


def _apply_manual_override(item: Item) -> None:
    if not has_manual_override(item):
        return

    override_status = (item.override_status or "unknown").strip().lower()
    if override_status not in OVERRIDE_STATUSES:
        override_status = "unknown"

    item.status = override_status
    item.effective_at = item.override_effective_at
    item.expires_at = item.override_expires_at
    item.replaced_by = item.override_replaced_by or ""
    item.last_checked_at = datetime.utcnow()
    item.status_reason = f"manual_override:{(item.override_reason or '').strip() or 'operator'}"


def clear_manual_override(item: Item) -> None:
    item.override_enabled = False
    item.override_status = ""
    item.override_effective_at = None
    item.override_expires_at = None
    item.override_replaced_by = ""
    item.override_reason = ""
    item.override_updated_at = None


def apply_policy_lifecycle(item: Item) -> None:
    if not _is_policy_like(item):
        return
    updates = detect_policy_lifecycle(item)
    item.effective_at = updates["effective_at"]
    item.expires_at = updates["expires_at"]
    item.status = updates["status"]
    item.replaced_by = updates["replaced_by"]
    item.last_checked_at = updates["last_checked_at"]
    item.status_reason = updates["status_reason"]
    _apply_manual_override(item)


def audit_policy_lifecycle(item: Item, *, successor_exists: bool = False, now: datetime | None = None) -> dict[str, Any]:
    point = now or datetime.utcnow()
    codes: list[str] = []
    status = (item.status or "unknown").strip() or "unknown"
    text = _combined_text(item.title or "", item.summary or "", item.raw_content or "")
    published = item.published_at or item.fetched_at

    if status == "unknown":
        codes.append("unknown_status")
    if item.last_checked_at is None or item.last_checked_at < point - timedelta(days=14):
        codes.append("stale_check")
    if status == "superseded" and (item.replaced_by or "").strip() and not successor_exists:
        codes.append("missing_successor")
    if status == "active" and item.effective_at and item.effective_at > point:
        codes.append("future_effective")
    if item.expires_at and item.expires_at < point and status != "inactive":
        codes.append("expired_status_mismatch")
    if status == "draft" and published and published < point - timedelta(days=180):
        codes.append("draft_stale")
    if status == "active" and item.source_id in OFFICIAL_POLICY_SOURCES and published and published < point - timedelta(days=365) and item.expires_at is None:
        codes.append("long_running_no_expiry")
    if status == "inactive" and item.expires_at is None and not any(hint in text for hint in INACTIVE_HINTS):
        codes.append("inactive_without_signal")

    severity_order = {
        "expired_status_mismatch": 0,
        "missing_successor": 1,
        "unknown_status": 2,
        "future_effective": 3,
        "draft_stale": 4,
        "stale_check": 5,
        "long_running_no_expiry": 6,
        "inactive_without_signal": 7,
    }
    ordered = sorted(dict.fromkeys(codes), key=lambda code: severity_order.get(code, 99))
    tone = "warning" if ordered and severity_order.get(ordered[0], 99) <= 3 else "pending" if ordered else "neutral"
    return {"codes": ordered, "tone": tone}


def link_superseded_policies(session: Session, current_item: Item) -> None:
    text = _combined_text(current_item.title or "", current_item.summary or "", current_item.raw_content or "")
    retired_titles = _extract_retired_titles(text)
    if not retired_titles:
        return

    for retired_title in retired_titles:
        rows = session.execute(
            select(Item).where(Item.id != current_item.id, Item.title.contains(retired_title))
        ).scalars().all()
        for row in rows:
            if has_manual_override(row):
                continue
            row.status = "superseded"
            row.replaced_by = current_item.title
            row.last_checked_at = datetime.utcnow()
            row.status_reason = f"replaced_by:{current_item.title}"


def refresh_policy_lifecycle(session: Session, *, commit: bool = False) -> None:
    rows = session.execute(select(Item)).scalars().all()
    for row in rows:
        if _is_policy_like(row):
            apply_policy_lifecycle(row)
    if commit:
        session.commit()

