from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

from app.schemas import CollectedItem
from app.utils.normalize import normalize_url


def canonical_title(title: str) -> str:
    lowered = (title or '').lower().strip()
    lowered = re.sub(r'\[[^\]]+\]|\([^\)]+\)|【[^】]+】', ' ', lowered)
    lowered = re.sub(r'\s+[\-|｜|]\s+[^\-|｜|]{1,32}$', ' ', lowered)
    lowered = re.sub(r'[^\w一-鿿]+', ' ', lowered)
    lowered = re.sub(r'\s+', ' ', lowered)
    return lowered.strip()


def _title_tokens(title: str) -> set[str]:
    return {token for token in canonical_title(title).split() if len(token) > 1}


def title_similarity(left: str, right: str) -> float:
    left_clean = canonical_title(left)
    right_clean = canonical_title(right)
    if not left_clean or not right_clean:
        return 0.0
    seq_score = SequenceMatcher(None, left_clean, right_clean).ratio()
    left_tokens = _title_tokens(left_clean)
    right_tokens = _title_tokens(right_clean)
    if not left_tokens or not right_tokens:
        return seq_score
    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(seq_score, overlap)


def titles_look_duplicate(left: str, right: str) -> bool:
    left_clean = canonical_title(left)
    right_clean = canonical_title(right)
    if not left_clean or not right_clean:
        return False
    if left_clean == right_clean:
        return True
    seq_score = SequenceMatcher(None, left_clean, right_clean).ratio()
    left_tokens = _title_tokens(left_clean)
    right_tokens = _title_tokens(right_clean)
    token_union = left_tokens | right_tokens
    if len(token_union) <= 1 and seq_score < 0.95:
        return False
    shorter, longer = sorted((left_clean, right_clean), key=len)
    if len(shorter) >= 14 and shorter in longer and len(shorter) / max(len(longer), 1) >= 0.72:
        return True
    return title_similarity(left_clean, right_clean) >= 0.88


def _published_close(left: datetime | None, right: datetime | None, window_days: int = 3) -> bool:
    if left is None or right is None:
        return True
    return abs((left - right).total_seconds()) <= window_days * 86400


def items_look_duplicate(
    title: str,
    published_at: datetime | None,
    other_title: str,
    other_published_at: datetime | None,
    url: str = '',
    other_url: str = '',
) -> bool:
    if url and other_url and normalize_url(url) == normalize_url(other_url):
        return True
    return titles_look_duplicate(title, other_title) and _published_close(published_at, other_published_at)


def _quality_score(item: CollectedItem) -> tuple[int, int, int]:
    content_len = len((item.raw_content or '').strip())
    summary_len = len((item.raw_summary or '').strip())
    official_hint = int(item.source_id.endswith('-policy') or item.source_id.startswith(('openai-', 'anthropic-', 'deepmind-', 'huggingface-', 'arxiv-')))
    return (official_hint, content_len, summary_len)


def deduplicate_items(items: list[CollectedItem]) -> list[CollectedItem]:
    selected: list[CollectedItem] = []
    for item in items:
        duplicate_index: int | None = None
        for index, existing in enumerate(selected):
            if items_look_duplicate(item.title, item.published_at, existing.title, existing.published_at, item.url, existing.url):
                duplicate_index = index
                break
        if duplicate_index is None:
            selected.append(item)
            continue
        if _quality_score(item) > _quality_score(selected[duplicate_index]):
            selected[duplicate_index] = item
    return selected
