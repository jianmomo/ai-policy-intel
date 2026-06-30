from datetime import datetime
from pathlib import Path

from app.scoring.engine import ScoreEngine
from app.schemas import CollectedItem, SourceDefinition


def test_scoring_returns_positive_score() -> None:
    engine = ScoreEngine(Path("configs/scoring.yaml"))
    item = CollectedItem(
        source_id="s",
        title="AI subsidy policy for agriculture",
        url="https://example.com",
        published_at=datetime.utcnow(),
        raw_summary="subsidy agriculture ai",
        raw_content="",
        tags=["policy", "subsidy"],
    )
    source = SourceDefinition(
        id="s",
        name="Source",
        category="policy",
        region="cn",
        type="html",
        url="https://example.com",
        enabled=True,
        priority=10,
        tags=["policy", "subsidy"],
    )
    score, reason = engine.score(item, source, "Policy-Subsidy")
    assert score > 0
    assert reason

