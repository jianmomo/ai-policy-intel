from sqlalchemy.orm import Session

from app.db.models import OSSProject


def seed_default_oss_projects(session: Session) -> None:
    if session.query(OSSProject).count():
        return

    defaults = [
        ("RSSHub", "https://github.com/DIYgod/RSSHub", "feed", "Public feed adapter candidate"),
        ("Horizon", "https://github.com/Thysrael/Horizon", "digest", "AI-powered enrichment candidate"),
        ("RSSBrew", "https://github.com/yinan-c/RSSBrew", "digest", "Digest comparison candidate"),
    ]
    for name, repo_url, category, note in defaults:
        session.add(OSSProject(name=name, repo_url=repo_url, category=category, note=note))
    session.commit()

