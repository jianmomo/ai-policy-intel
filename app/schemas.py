from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SourceDefinition:
    id: str
    name: str
    category: str
    region: str
    type: str
    url: str
    enabled: bool
    priority: int
    tags: list[str] = field(default_factory=list)
    query: str = ''
    max_results: int = 10
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectedItem:
    source_id: str
    title: str
    url: str
    published_at: datetime | None
    raw_summary: str = ''
    raw_content: str = ''
    region: str = ''
    tags: list[str] = field(default_factory=list)


@dataclass
class ClassificationResult:
    category: str
    tags: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
