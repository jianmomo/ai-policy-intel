from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Item, OSSProject
from app.utils.dedup import items_look_duplicate


def render_digest(session: Session, output_path: Path, title: str, limit: int, items: list[Item] | None = None) -> None:
    candidates = items if items is not None else session.execute(select(Item).order_by(Item.score.desc(), Item.fetched_at.desc()).limit(limit)).scalars().all()
    rows: list[Item] = []
    for row in sorted(candidates, key=lambda value: (value.score or 0.0, value.fetched_at or value.published_at or datetime.min), reverse=True):
        if any(items_look_duplicate(row.title, row.published_at or row.fetched_at, current.title, current.published_at or current.fetched_at, row.url, current.url) for current in rows):
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    grouped: dict[str, list[Item]] = defaultdict(list)
    for row in rows:
        grouped[row.category].append(row)

    lines: list[str] = [f"# {title}", "", f"- Generated at: {datetime.utcnow().isoformat()}Z", f"- Items: {len(rows)}", ""]
    if not rows:
        lines.extend(["- No new items in this window.", ""])
    for category in sorted(grouped):
        lines.append(f"## {category}")
        lines.append("")
        for row in grouped[category]:
            published = row.published_at.isoformat() if row.published_at else "unknown"
            lines.append(f"- [{row.title}]({row.url})")
            lines.append(f"  Source: {row.source_id} | Published: {published} | Score: {row.score:.1f}")
            if row.subcategory:
                lines.append(f"  Tags: {row.subcategory}")
            lines.append(f"  Reason: {row.reason}")
            if row.summary:
                lines.append(f"  Summary: {row.summary}")
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def render_oss_radar(session: Session, output_path: Path) -> None:
    rows = session.execute(select(OSSProject).order_by(OSSProject.created_at.desc())).scalars().all()
    lines = ["# OSS Radar", "", f"- Generated at: {datetime.utcnow().isoformat()}Z", ""]
    if not rows:
        lines.extend(
            [
                "- Horizon | AI digest orchestration candidate",
                "- RSSHub | feed adapter candidate",
                "- RSSBrew | digest comparison candidate",
            ]
        )
    else:
        for row in rows:
            lines.append(f"- [{row.name}]({row.repo_url}) | {row.category} | {row.note}")
    output_path.write_text("\n".join(lines), encoding="utf-8")
