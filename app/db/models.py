from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, engine


IS_SQLITE = engine.dialect.name == "sqlite"
POLICY_TABLE_ARGS = {} if IS_SQLITE else {"schema": "policy_intel"}


class UTCNaiveDateTime(TypeDecorator):
    """Store UTC in the database and preserve legacy UTC-naive app values."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = POLICY_TABLE_ARGS

    id: Mapped[str] = mapped_column("id" if IS_SQLITE else "source_key", String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column("type" if IS_SQLITE else "source_type", String(32))
    url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    tags: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCNaiveDateTime(), default=datetime.utcnow)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = POLICY_TABLE_ARGS

    id: Mapped[int] = mapped_column("id" if IS_SQLITE else "legacy_id", Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        "source_id" if IS_SQLITE else "source_key",
        ForeignKey("sources.id" if IS_SQLITE else "policy_intel.sources.source_key")
    )
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(UTCNaiveDateTime(), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCNaiveDateTime(), default=datetime.utcnow)
    category: Mapped[str] = mapped_column(String(64), default="Unclassified")
    subcategory: Mapped[str] = mapped_column(String(64), default="")
    region: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    raw_content: Mapped[str] = mapped_column("raw_content" if IS_SQLITE else "content_text", Text, default="")
    hash: Mapped[str] = mapped_column("hash" if IS_SQLITE else "content_hash", String(128), unique=True)
    effective_at: Mapped[datetime | None] = mapped_column(UTCNaiveDateTime(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCNaiveDateTime(), nullable=True)
    status: Mapped[str] = mapped_column("status" if IS_SQLITE else "lifecycle_status", String(32), default="unknown")
    replaced_by: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCNaiveDateTime(), nullable=True)
    status_reason: Mapped[str] = mapped_column(Text, default="")
    override_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    override_status: Mapped[str] = mapped_column(String(32), default="")
    override_effective_at: Mapped[datetime | None] = mapped_column(UTCNaiveDateTime(), nullable=True)
    override_expires_at: Mapped[datetime | None] = mapped_column(UTCNaiveDateTime(), nullable=True)
    override_replaced_by: Mapped[str] = mapped_column(Text, default="")
    override_reason: Mapped[str] = mapped_column(Text, default="")
    override_updated_at: Mapped[datetime | None] = mapped_column(UTCNaiveDateTime(), nullable=True)

    source: Mapped[Source] = relationship()


class RunLog(Base):
    __tablename__ = "runs"
    __table_args__ = POLICY_TABLE_ARGS

    id: Mapped[int] = mapped_column("id" if IS_SQLITE else "legacy_id", Integer, primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCNaiveDateTime(), default=datetime.utcnow)


class MissedItem(Base):
    __tablename__ = "missed_items"
    __table_args__ = POLICY_TABLE_ARGS

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCNaiveDateTime(), default=datetime.utcnow)


class OSSProject(Base):
    __tablename__ = "oss_projects"
    __table_args__ = POLICY_TABLE_ARGS

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    repo_url: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCNaiveDateTime(), default=datetime.utcnow)


class TopicSnapshot(Base):
    __tablename__ = "topic_snapshots"
    __table_args__ = POLICY_TABLE_ARGS

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    topic: Mapped[str] = mapped_column(String(255))
    window_days: Mapped[int] = mapped_column(Integer)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    official_count: Mapped[int] = mapped_column(Integer, default=0)
    broad_count: Mapped[int] = mapped_column(Integer, default=0)
    supplement_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    latest_at: Mapped[datetime | None] = mapped_column(UTCNaiveDateTime(), nullable=True)
    snapshot_date: Mapped[str] = mapped_column("snapshot_date" if IS_SQLITE else "snapshot_date_text", String(16))
    snapshot_at: Mapped[datetime] = mapped_column("snapshot_at" if IS_SQLITE else "created_at", UTCNaiveDateTime(), default=datetime.utcnow)
