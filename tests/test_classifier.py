from pathlib import Path

from app.classifiers.keywords import KeywordClassifier
from app.schemas import CollectedItem


def test_keyword_classifier_matches_ai_research() -> None:
    classifier = KeywordClassifier(Path("configs/keywords.yaml"))
    item = CollectedItem(
        source_id="x",
        title="New multimodal training benchmark",
        url="https://example.com",
        published_at=None,
        raw_summary="A new large language model training method.",
        raw_content="",
    )
    result = classifier.classify(item)
    assert result.category == "AI-Research"
    assert "benchmark" in [keyword.lower() for keyword in result.matched_keywords]


def test_keyword_classifier_extracts_policy_tags() -> None:
    classifier = KeywordClassifier(Path("configs/keywords.yaml"))
    item = CollectedItem(
        source_id="p",
        title="国务院办公厅关于进一步完善大中型水库移民后期扶持政策的通知",
        url="https://example.com/policy",
        published_at=None,
        raw_summary="农业农村 补贴 扶持 政策",
        raw_content="",
    )
    result = classifier.classify(item)
    assert result.category == "Policy-Central"
    assert "Central-Policy" in result.tags
    assert "Subsidy" in result.tags


def test_keyword_classifier_avoids_short_keyword_false_positive() -> None:
    classifier = KeywordClassifier(Path("configs/keywords.yaml"))
    item = CollectedItem(
        source_id="n",
        title="AI Builds Complete Languages From Scratch: ACL Paper Tops Every Prior Conlang System",
        url="https://example.com/news",
        published_at=None,
        raw_summary="A model and benchmark update.",
        raw_content="",
    )
    result = classifier.classify(item)
    assert result.category != "Energy"
