from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Item, OSSProject


def render_digest(session: Session, output_path: Path, title: str, limit: int) -> None:
    rows = session.execute(select(Item).order_by(Item.score.desc(), Item.fetched_at.desc()).limit(limit)).scalars().all()
    grouped: dict[str, list[Item]] = defaultdict(list)
    for row in rows:
        grouped[row.category].append(row)

    lines: list[str] = [f"# {title}", "", f"- Generated at: {datetime.utcnow().isoformat()}Z", f"- Items: {len(rows)}", ""]
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
