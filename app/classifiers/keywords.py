from pathlib import Path
import re

import yaml

from app.schemas import ClassificationResult, CollectedItem


class KeywordClassifier:
    def __init__(self, config_path: Path) -> None:
        content = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.category_keywords: dict[str, list[str]] = content.get("categories", {})
        self.tag_keywords: dict[str, list[str]] = content.get("tags", {})

    @staticmethod
    def _contains_keyword(haystack: str, keyword: str) -> bool:
        normalized = keyword.strip().lower()
        if not normalized:
            return False

        # Use word-aware matching for ASCII terms to avoid false positives like "ev" in "every".
        if re.fullmatch(r"[a-z0-9][a-z0-9 \-_\.]*", normalized):
            pattern = r"(?<![a-z0-9])" + re.escape(normalized) + r"(?![a-z0-9])"
            return re.search(pattern, haystack) is not None

        return normalized in haystack

    def classify(self, item: CollectedItem) -> ClassificationResult:
        haystack = f"{item.title} {item.raw_summary} {item.raw_content}".lower()
        best_category = "Unclassified"
        best_score = 0
        matched_keywords: list[str] = []
        for category, keywords in self.category_keywords.items():
            hits = [keyword for keyword in keywords if self._contains_keyword(haystack, keyword)]
            score = len(hits)
            if score > best_score:
                best_category = category
                best_score = score
                matched_keywords = hits

        tags = self.extract_tags(item)
        return ClassificationResult(category=best_category, tags=tags, matched_keywords=matched_keywords)

    def extract_tags(self, item: CollectedItem) -> list[str]:
        haystack = f"{item.title} {item.raw_summary} {item.raw_content}".lower()
        tags: list[str] = []
        for tag, keywords in self.tag_keywords.items():
            if any(self._contains_keyword(haystack, keyword) for keyword in keywords):
                tags.append(tag)

        for source_tag in item.tags:
            normalized = source_tag.strip()
            if normalized and normalized not in tags:
                tags.append(normalized)

        return tags
