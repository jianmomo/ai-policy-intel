from datetime import datetime, timedelta
from pathlib import Path

import yaml

from app.schemas import CollectedItem, SourceDefinition


class ScoreEngine:
    def __init__(self, config_path: Path) -> None:
        content = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.weights: dict[str, float] = content.get("weights", {})

    def score(self, item: CollectedItem, source: SourceDefinition, category: str) -> tuple[float, str]:
        score = float(source.priority) * float(self.weights.get("source_priority_multiplier", 1.0))
        reason_bits: list[str] = [f"priority={source.priority}"]

        text = f"{item.title} {item.raw_summary} {item.raw_content}".lower()
        tag_hits = sum(1 for tag in source.tags if tag.lower() in text)
        score += tag_hits * float(self.weights.get("keyword_match", 1.0))
        if tag_hits:
            reason_bits.append(f"tag_hits={tag_hits}")

        if item.published_at and item.published_at >= datetime.utcnow() - timedelta(days=2):
            score += float(self.weights.get("fresh_item_bonus", 0.0))
            reason_bits.append("fresh")

        if category.startswith("Policy-") and source.region == "cn":
            score += float(self.weights.get("official_source_bonus", 0.0))
            reason_bits.append("policy_bonus")

        if len(item.tags) >= 2:
            score += float(self.weights.get("multi_tag_bonus", 0.0))
            reason_bits.append("multi_tag")

        return score, ", ".join(reason_bits)

